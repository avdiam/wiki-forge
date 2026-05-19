#!/usr/bin/env python3
"""Append one row to wiki/.meta/ingest_log.jsonl.

Called by the /wiki-ingest skill after the ingestor subagent returns.
Cost fields are parsed by the skill from the `<usage>` block the Agent
tool surfaces on subagent completion; pass empty string or "null" when
they aren't available.

Row schema:
    {
      "timestamp": "2026-04-20T14:30:05",
      "source_path": "vault/Web Articles/2026-04-15-foo.md",
      "source_type": "article",
      "source_word_count": 3136,
      "pages_created": ["2026-04-15-foo", "bar"],
      "pages_updated": ["baz"],
      "contradictions_flagged": 0,
      "total_tokens": 158204,
      "input_tokens": 122,
      "cache_creation_input_tokens": 18432,
      "cache_read_input_tokens": 138210,
      "output_tokens": 1440,
      "tool_uses": 47,
      "duration_ms": 312405,
      "commit_hash": "a1b2c3d"
    }

The four per-field token counts (input / cache_creation_input /
cache_read_input / output) are surfaced by the Agent tool's `<usage>`
block when present and captured per-field — any subset may be `null`
if the block omits a field. `cache_creation_input_tokens` is the
load-bearing slice for measuring cache savings from auto-loaded context
(e.g., the SessionStart hook); `cache_read_input_tokens` is the
cache-hit volume that scales with replayed conversation context.
`total_tokens` remains the aggregate.

Usage:
    python append_ingest_log.py --wiki <wiki-root> \
        --source-path "vault/Web Articles/2026-04-15-foo.md" \
        --source-type article --source-word-count 3136 \
        --pages-created "2026-04-15-foo,bar" \
        --pages-updated "baz" \
        --contradictions-flagged 0 \
        --total-tokens 158204 --tool-uses 47 --duration-ms 312405 \
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


VALID_SOURCE_TYPES = {"article", "paper", "gist", "blog", "docs", "transcript", "notes", "other"}


def optional_int(raw: str) -> int | None:
    """Accept empty string or 'null' as None; otherwise coerce to int."""
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return int(raw)


def split_slugs(raw: str) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    p.add_argument("--source-path", required=True, help="Project-relative path to the raw source")
    p.add_argument("--source-type", required=True, help=f"One of: {', '.join(sorted(VALID_SOURCE_TYPES))}")
    p.add_argument("--source-word-count", type=int, required=True)
    p.add_argument("--pages-created", default="", help="Comma-separated slugs")
    p.add_argument("--pages-updated", default="", help="Comma-separated slugs")
    p.add_argument("--contradictions-flagged", type=int, default=0)
    p.add_argument("--total-tokens", type=optional_int, default=None, help="From <usage> block; '' or 'null' allowed")
    p.add_argument("--input-tokens", type=optional_int, default=None, help="From <usage>; null when not surfaced")
    p.add_argument("--cache-creation-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-write field (load-bearing for SessionStart-hook savings)")
    p.add_argument("--cache-read-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-hit field")
    p.add_argument("--output-tokens", type=optional_int, default=None, help="From <usage>; null when not surfaced")
    p.add_argument("--tool-uses", type=optional_int, default=None, help="From <usage> block; '' or 'null' allowed")
    p.add_argument("--duration-ms", type=int, required=True, help="Wall-clock ms for the ingest")
    p.add_argument("--commit-hash", required=True)
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source_path": args.source_path,
        "source_type": args.source_type,
        "source_word_count": args.source_word_count,
        "pages_created": split_slugs(args.pages_created),
        "pages_updated": split_slugs(args.pages_updated),
        "contradictions_flagged": args.contradictions_flagged,
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

    if args.source_type not in VALID_SOURCE_TYPES:
        print(
            f"error: invalid source_type '{args.source_type}'. "
            f"Valid: {', '.join(sorted(VALID_SOURCE_TYPES))}",
            file=sys.stderr,
        )
        return 2

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    meta_dir = wiki_root / ".meta"
    meta_dir.mkdir(exist_ok=True)
    log_path = meta_dir / "ingest_log.jsonl"

    row = build_row(args)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
