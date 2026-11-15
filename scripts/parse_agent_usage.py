#!/usr/bin/env python3
"""Parse Agent-tool `<usage>` blocks into one telemetry JSON for the appenders.

Every skill that dispatches a subagent (`/wiki-ingest`, `/wiki-lint`,
`/wiki-query`, `/wiki-summarize`, `/wiki-overview`) logs that dispatch's cost.
The harness reports it in a `<usage>` block, and the block's syntax has
changed over time, so this one script owns the parsing instead of each SKILL
carrying its own regex prose.

Accepted syntaxes (both may appear; every block in the input is summed):

    <usage>total_tokens: 107145, tool_uses: 49, duration_ms: 428134</usage>
    <usage><subagent_tokens>84332</subagent_tokens><tool_uses>7</tool_uses>
           <duration_ms>40048</duration_ms></usage>

`subagent_tokens` maps to `total_tokens`. Field names are matched with a
negative lookbehind, so `input_tokens` never matches inside
`cache_read_input_tokens` (the old SKILL regex quirk).

Multiple blocks (a resumed agent's fix turn, or Mode B's two passes) are
summed field by field, including `duration_ms`: each block reports its own
turn, so the sum is the operation's agent time.

Optional transcript enrichment (`--agent-id`, repeatable): reads
`~/.claude/projects/<slug>/*/subagents/agent-<id>.jsonl` and adds
- `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`,
  `output_tokens`: summed over every assistant API call (the billing view;
  these do NOT add up to `total_tokens`, which is the harness's own figure
  and tracks context size, not work: s135's linter reported 47K while its
  turns re-read ~1.0M cached tokens. Judge efficiency by the non-cached
  sums, `input_tokens` + `cache_creation_input_tokens` (+ `output_tokens`):
  they cost ~10x a cache read),
- `models`: the distinct per-message `model` values (the model that actually
  ran; never trust `.meta.json`'s `pickedEffortModel`),
- `assistant_turns`, and `tool_uses` when no block supplied it,
- `duration_ms` when no block supplied it: the sum of the transcript's
  per-turn time spans (`duration_source: "transcript"`).

Output: one JSON object on stdout (and to `--out` if given), with every
appender cost field present (null when unknown). Pass that file to an
appender with `--usage-json <path>`. Stdlib only; Python 3.9-safe.

Usage:
    python parse_agent_usage.py --out .claude/tmp/usage.json <<'USAGE'
    <usage><subagent_tokens>84332</subagent_tokens>...</usage>
    USAGE

    python parse_agent_usage.py --agent-id a66d9773c211ebe6b --out u.json < blocks.txt
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

COST_FIELDS = (
    "total_tokens",
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "tool_uses",
    "duration_ms",
)
# Source name in the block -> appender field.
ALIASES = {"subagent_tokens": "total_tokens"}
TRANSCRIPT_TOKEN_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)

_BLOCK_RE = re.compile(r"<usage>(.*?)</usage>", re.DOTALL)


def _field_re(name: str) -> re.Pattern:
    # `name: N` or `<name>N` ; never a suffix of a longer identifier.
    return re.compile(rf"(?<![A-Za-z0-9_]){name}(?:\s*:\s*|\s*>\s*)(\d+)")


_SOURCE_NAMES = tuple(COST_FIELDS) + tuple(ALIASES)
_FIELD_RES = {name: _field_re(name) for name in _SOURCE_NAMES}


def parse_block(body: str) -> dict:
    """Parse one `<usage>` body. Returns {field: int} for fields found."""
    out: dict = {}
    for name in _SOURCE_NAMES:
        m = _FIELD_RES[name].search(body)
        if not m:
            continue
        field = ALIASES.get(name, name)
        # An explicit total_tokens wins over the alias if both appear.
        if field in out and name in ALIASES:
            continue
        out[field] = int(m.group(1))
    return out


def parse_usage_text(text: str) -> dict:
    """Sum every `<usage>` block in `text`. Missing fields -> None."""
    totals: dict = {f: None for f in COST_FIELDS}
    blocks = _BLOCK_RE.findall(text or "")
    for body in blocks:
        for field, val in parse_block(body).items():
            totals[field] = val if totals[field] is None else totals[field] + val
    totals["blocks"] = len(blocks)
    return totals


def project_slug(cwd: str) -> str:
    """Claude Code's project-dir slug: every :, \\, / -> -."""
    return re.sub(r"[:\\/]", "-", os.path.abspath(cwd))


def find_agent_transcript(agent_id: str, cwd: str, projects_root: str | None = None) -> str | None:
    root = projects_root or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    pattern = os.path.join(root, project_slug(cwd), "*", "subagents", f"agent-{agent_id}.jsonl")
    hits = glob.glob(pattern)
    return max(hits, key=os.path.getmtime) if hits else None


def read_transcript(path: str) -> dict:
    sums = {f: 0 for f in TRANSCRIPT_TOKEN_FIELDS}
    models: list = []
    turns = 0
    tool_uses = 0
    seen: set = set()
    spans: dict = {}  # promptId -> [first_ts, last_ts]; one entry per turn
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = _parse_ts(rec.get("timestamp"))
            if ts is not None:
                span = spans.setdefault(rec.get("promptId"), [ts, ts])
                span[0] = min(span[0], ts)
                span[1] = max(span[1], ts)
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            # One API call is written as several records (one per content
            # block) sharing message.id and the same usage: count it once.
            mid = msg.get("id")
            first = mid is None or mid not in seen
            if mid is not None:
                seen.add(mid)
            model = msg.get("model")
            if model and model not in models:
                models.append(model)
            if first:
                turns += 1
                usage = msg.get("usage") or {}
                for f in TRANSCRIPT_TOKEN_FIELDS:
                    sums[f] += usage.get(f) or 0
            content = msg.get("content")
            if isinstance(content, list):
                tool_uses += sum(1 for c in content if isinstance(c, dict) and c.get("type") == "tool_use")
    span_ms = sum(int((b - a).total_seconds() * 1000) for a, b in spans.values()) if spans else None
    return {"sums": sums, "models": models, "assistant_turns": turns, "tool_uses": tool_uses,
            "span_ms": span_ms}


def _parse_ts(raw):
    """ISO-8601 transcript timestamp ('...Z') -> aware datetime, or None."""
    if not isinstance(raw, str):
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def enrich(result: dict, agent_ids: list, cwd: str, projects_root: str | None = None) -> dict:
    sums = {f: 0 for f in TRANSCRIPT_TOKEN_FIELDS}
    models: list = []
    turns = 0
    tool_uses = 0
    span_ms = None
    missing = []
    found = 0
    for aid in agent_ids:
        path = find_agent_transcript(aid, cwd, projects_root)
        if not path:
            missing.append(aid)
            continue
        found += 1
        t = read_transcript(path)
        for f in TRANSCRIPT_TOKEN_FIELDS:
            sums[f] += t["sums"][f]
        models += [m for m in t["models"] if m not in models]
        turns += t["assistant_turns"]
        tool_uses += t["tool_uses"]
        if t.get("span_ms") is not None:
            span_ms = (span_ms or 0) + t["span_ms"]
    if found:
        for f in TRANSCRIPT_TOKEN_FIELDS:
            if result.get(f) is None:
                result[f] = sums[f]
        if result.get("tool_uses") is None:
            result["tool_uses"] = tool_uses
        result["models"] = models
        result["assistant_turns"] = turns
        # No <usage> block (or one without duration): fall back to the
        # transcript's per-turn time spans, so no caller needs wall-clock
        # bookkeeping. Gaps between resumed turns are excluded.
        if result.get("duration_ms") is None and span_ms is not None:
            result["duration_ms"] = span_ms
            result["duration_source"] = "transcript"
    if missing:
        result["missing_transcripts"] = missing
    return result


def main(argv: list) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--text", help="Usage text; default reads stdin")
    p.add_argument("--agent-id", action="append", default=[],
                   help="Subagent id to enrich from its transcript (repeatable)")
    p.add_argument("--cwd", default=os.getcwd(), help="Project root for slug resolution (default: cwd)")
    p.add_argument("--projects-root", help="Override ~/.claude/projects (tests)")
    p.add_argument("--out", help="Also write the JSON here")
    args = p.parse_args(argv)

    text = args.text if args.text is not None else ("" if sys.stdin.isatty() else sys.stdin.read())
    result = parse_usage_text(text)
    if args.agent_id:
        enrich(result, args.agent_id, args.cwd, args.projects_root)

    payload = json.dumps(result, ensure_ascii=False)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
