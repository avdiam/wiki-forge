#!/usr/bin/env python3
"""Append one row to wiki/.meta/overview_log.jsonl.

Called by the /wiki-overview skill on Mode A (soft refresh) and Mode B
(full rewrite) return — one row per dispatch. Cost fields are parsed by
the skill from the `<usage>` block surfaced by the Agent tool on
curator-subagent return; pass empty string or "null" when they aren't
available.

Mode B emits a single row that aggregates Pass 1 + Pass 2 token / turn
counts (per the HITL contract in vault/.wiki-identity.md — "each Pass
emits its own <usage>. Aggregate total_tokens / tool_uses / duration_ms
across passes"). `commit_hash` is from the Mode B Pass-2 commit only.

Schema documented in wiki/CLAUDE.md under "Telemetry".

Usage:
    python append_overview_log.py --wiki <wiki-root> \\
        --mode soft \\
        --ingest-count-at-run 120 \\
        --status applied \\
        --total-tokens 28100 --tool-uses 9 \\
        --input-tokens 60 --cache-creation-input-tokens 4200 \\
        --cache-read-input-tokens 22300 --output-tokens 540 \\
        --duration-ms 41200 \\
        --commit-hash a1b2c3d
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


VALID_MODES = {"soft", "full"}
VALID_STATUSES = {"applied", "no_changes", "cancelled"}


def optional_int(raw: str) -> int | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return int(raw)


def optional_str(raw: str) -> str | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return raw


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append a row to .meta/overview_log.jsonl")
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    p.add_argument("--mode", required=True, help=f"One of: {', '.join(sorted(VALID_MODES))}")
    p.add_argument("--trigger", type=optional_str, default=None,
                   help="Mode B: trigger reasons string or 'user-invoked'. Null for soft.")
    p.add_argument("--ingest-count-at-run", type=int, required=True,
                   help="Snapshot count of ingest entries in log.md at dispatch time")
    p.add_argument("--status", required=True,
                   help=f"One of: {', '.join(sorted(VALID_STATUSES))}")
    p.add_argument("--total-tokens", type=optional_int, default=None,
                   help="Aggregate across passes (Mode B); from <usage>")
    p.add_argument("--input-tokens", type=optional_int, default=None,
                   help="From <usage>; null when not surfaced")
    p.add_argument("--cache-creation-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-write field")
    p.add_argument("--cache-read-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-hit field")
    p.add_argument("--output-tokens", type=optional_int, default=None,
                   help="From <usage>; null when not surfaced")
    p.add_argument("--tool-uses", type=optional_int, default=None,
                   help="Aggregate across passes (Mode B); from <usage>")
    p.add_argument("--duration-ms", type=int, required=True,
                   help="Wall-clock ms for the whole operation (sum of passes for Mode B)")
    p.add_argument("--commit-hash", type=optional_str, default=None,
                   help="Null when status=no_changes or cancelled")
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "trigger": args.trigger,
        "ingest_count_at_run": args.ingest_count_at_run,
        "status": args.status,
        "total_tokens": args.total_tokens,
        "input_tokens": args.input_tokens,
        "cache_creation_input_tokens": args.cache_creation_input_tokens,
        "cache_read_input_tokens": args.cache_read_input_tokens,
        "output_tokens": args.output_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
        "commit_hash": args.commit_hash,
    }


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.mode not in VALID_MODES:
        print(f"error: invalid mode '{args.mode}'. "
              f"Valid: {', '.join(sorted(VALID_MODES))}", file=sys.stderr)
        return 2
    if args.status not in VALID_STATUSES:
        print(f"error: invalid status '{args.status}'. "
              f"Valid: {', '.join(sorted(VALID_STATUSES))}", file=sys.stderr)
        return 2

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    meta_dir = wiki_root / ".meta"
    meta_dir.mkdir(exist_ok=True)
    log_path = meta_dir / "overview_log.jsonl"

    row = build_row(args)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
