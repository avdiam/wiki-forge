#!/usr/bin/env python3
"""Aggregate token / tool cost over a window of a Claude Code session transcript.

The Claude Code session transcript JSONL (one file per session under
`~/.claude/projects/<project-slug>/<session-id>.jsonl`) records a `usage`
object on every assistant turn:

    {"input_tokens": N, "cache_creation_input_tokens": N,
     "cache_read_input_tokens": N, "output_tokens": N,
     "server_tool_use": {"web_search_requests": N, "web_fetch_requests": N}}

plus a millisecond ISO `timestamp` and the turn's `content` blocks. NOTE:
WebSearch in Claude Code is a *client/harness* tool (a `tool_use` block named
`WebSearch`), NOT the Anthropic API's server-side `web_search` tool, so
`server_tool_use.web_search_requests` stays 0 for main-session work — the call
count must come from counting `WebSearch` tool_use blocks by name. The probe
counts both surfaces and sums them (mutually exclusive per environment). This
makes main-session cost recoverable *after the fact* even though the harness
emits no `<usage>` block for main-session (non-subagent) work — the surface that
`/wiki-corroborate` instrumentation needs to answer the inline-vs-fork ROI
question (dev/tech-debt.md "execution shape" entry).

The probe reads a **line-window** of one transcript. A caller records the
transcript's line count immediately before a unit of work (`--since-line N`,
the count at start); the probe then aggregates every assistant turn that landed
after line N. Window = `lines[N:]` (0-based), i.e. lines added since the marker.

Context-size convention: a turn's *context* is
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
(everything fed to the model that turn). `context_tokens_post_pass` is that sum
for the LAST assistant turn in the window; `context_tokens_pre_pass` is the same
for the last assistant turn BEFORE the window (so `context_growth` measures how
much the pass inflated main-session context — the load-bearing fork-decision
signal).

Session-file resolution (when `--session-file` is omitted): derive the project
slug from `--cwd` (default: current working directory) the way Claude Code does
— every `:`, `\\`, `/` in the absolute path becomes `-` — then pick the
most-recently-modified `*.jsonl` under `~/.claude/projects/<slug>/`. That is the
live session. Pass `--session-file` explicitly in tests or when two sessions
are open for the same project.

Output: a single compact JSON object on stdout. Stdlib only; Python 3.9-safe.

Usage:
    # Aggregate everything since line 940 of the live session:
    python session_cost_probe.py --since-line 940

    # Explicit transcript (tests / disambiguation):
    python session_cost_probe.py --session-file /path/to/<id>.jsonl --since-line 0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


def project_slug(cwd: str) -> str:
    """Replicate Claude Code's project-dir slug: every :, \\, / -> -."""
    return re.sub(r"[:\\/]", "-", os.path.abspath(cwd))


def resolve_session_file(session_file: str | None, cwd: str) -> str | None:
    if session_file:
        return session_file if os.path.isfile(session_file) else None
    proj = os.path.join(
        os.path.expanduser("~"), ".claude", "projects", project_slug(cwd)
    )
    if not os.path.isdir(proj):
        return None
    candidates = [
        os.path.join(proj, f) for f in os.listdir(proj) if f.endswith(".jsonl")
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def parse_ts(raw: str | None):
    """Parse an ISO timestamp; tolerate a trailing 'Z' on Python 3.9."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _context_size(usage: dict) -> int:
    return (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
    )


def aggregate(lines: list[str], since_line: int, until_line: int | None) -> dict:
    """Aggregate assistant-turn usage over lines[since_line:until_line]."""
    window = lines[since_line:until_line]

    # Pre-window context anchor: last assistant turn strictly before the window.
    context_pre = None
    for raw in reversed(lines[:since_line]):
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "assistant":
            context_pre = _context_size(d.get("message", {}).get("usage", {}))
            break

    agg = {
        "turns": 0,
        "output_tokens_pass": 0,
        "input_tokens_pass": 0,
        "cache_creation_pass": 0,
        "cache_read_pass": 0,
        "web_search_requests": 0,  # Anthropic API server-tool counter (subagents)
        "web_fetch_requests": 0,
        "websearch_tool_uses": 0,  # Claude Code client-tool blocks named WebSearch
        "webfetch_tool_uses": 0,
        "tool_uses": 0,
    }
    context_post = None
    first_ts = last_ts = None

    for raw in window:
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "assistant":
            continue
        msg = d.get("message", {})
        usage = msg.get("usage", {}) or {}
        agg["turns"] += 1
        agg["output_tokens_pass"] += usage.get("output_tokens") or 0
        agg["input_tokens_pass"] += usage.get("input_tokens") or 0
        agg["cache_creation_pass"] += usage.get("cache_creation_input_tokens") or 0
        agg["cache_read_pass"] += usage.get("cache_read_input_tokens") or 0
        stu = usage.get("server_tool_use") or {}
        agg["web_search_requests"] += stu.get("web_search_requests") or 0
        agg["web_fetch_requests"] += stu.get("web_fetch_requests") or 0
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                agg["tool_uses"] += 1
                name = block.get("name", "")
                if name == "WebSearch":
                    agg["websearch_tool_uses"] += 1
                elif name == "WebFetch":
                    agg["webfetch_tool_uses"] += 1
        context_post = _context_size(usage)
        ts = parse_ts(d.get("timestamp"))
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

    agg["context_tokens_pre_pass"] = context_pre
    agg["context_tokens_post_pass"] = context_post
    if context_pre is not None and context_post is not None:
        agg["context_growth"] = context_post - context_pre
    else:
        agg["context_growth"] = None

    if first_ts is not None and last_ts is not None:
        agg["duration_ms"] = int((last_ts - first_ts).total_seconds() * 1000)
    else:
        agg["duration_ms"] = None

    # Convenience roll-up mapping onto the corroborate_log cost fields.
    # "fresh" tokens = newly-billed throughput of the pass (excludes the
    # discounted cache-read replay of already-resident context).
    agg["total_tokens"] = (
        agg["output_tokens_pass"]
        + agg["input_tokens_pass"]
        + agg["cache_creation_pass"]
    )
    # WebSearch in Claude Code is a *client* tool (logged as a tool_use block
    # named "WebSearch"), NOT the Anthropic API server-side web_search tool — so
    # usage.server_tool_use.web_search_requests stays 0 for main-session work.
    # Count both surfaces; they're mutually exclusive per environment (client
    # tool in Claude Code main session vs server tool in API subagents), so the
    # sum is the true call count without double-counting.
    agg["websearch_calls"] = agg["websearch_tool_uses"] + agg["web_search_requests"]
    agg["webfetch_calls"] = agg["webfetch_tool_uses"] + agg["web_fetch_requests"]
    return agg


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--since-line",
        type=int,
        default=0,
        help="Aggregate assistant turns after this many leading lines (the "
        "transcript line count recorded at the start of the unit of work).",
    )
    p.add_argument(
        "--until-line",
        type=int,
        default=None,
        help="Stop at this line index (default: end of file).",
    )
    p.add_argument(
        "--session-file",
        default=None,
        help="Explicit transcript path. Default: newest *.jsonl for --cwd's project.",
    )
    p.add_argument(
        "--cwd",
        default=None,
        help="Working dir used to derive the project slug (default: real CWD).",
    )
    p.add_argument(
        "--emit-marker",
        action="store_true",
        help="Resolve the live transcript, print {session_file, start_line, "
        "batch_id} and exit. Call at the START of a unit of work; pass the "
        "start_line back via --since-line at the end.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cwd = args.cwd or os.getcwd()
    session_file = resolve_session_file(args.session_file, cwd)
    if session_file is None:
        print(
            json.dumps({"error": "no session transcript found", "cwd": cwd}),
            file=sys.stderr,
        )
        return 2

    with open(session_file, encoding="utf-8") as fh:
        lines = fh.readlines()

    if args.emit_marker:
        stem = os.path.splitext(os.path.basename(session_file))[0]
        start_line = len(lines)
        print(
            json.dumps(
                {
                    "session_file": session_file,
                    "start_line": start_line,
                    "batch_id": f"{stem[:8]}-{start_line}",
                }
            )
        )
        return 0

    if args.since_line < 0 or args.since_line > len(lines):
        print(
            json.dumps(
                {
                    "error": "since-line out of range",
                    "since_line": args.since_line,
                    "total_lines": len(lines),
                }
            ),
            file=sys.stderr,
        )
        return 2

    result = aggregate(lines, args.since_line, args.until_line)
    result["session_file"] = session_file
    result["since_line"] = args.since_line
    result["total_lines"] = len(lines)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
