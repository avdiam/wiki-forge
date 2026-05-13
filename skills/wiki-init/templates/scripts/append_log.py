#!/usr/bin/env python3
"""Append a structured entry to wiki/log.md.

Each entry looks like:

    ## [2026-04-15 14:30] ingest | Karpathy LLM Wiki gist
    - Created: sources/2026-04-15-karpathy-llm-wiki.md, concepts/llm-wiki-pattern.md
    - Updated: concepts/rag.md, entities/karpathy.md
    - Contradictions: 2
    - Commit: a1b2c3d
    - Note: optional freeform note

Usage:
    python append_log.py <op> --title "..." [--created "..."] [--updated "..."]
                             [--contradictions N] [--commit <hash>]
                             [--note "..."] --wiki <wiki-root>

Operations: ingest | capture | query-filed | lint | init | note | stub
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


VALID_OPS = {"ingest", "capture", "query-filed", "lint", "init", "note", "stub"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append a structured entry to wiki/log.md")
    p.add_argument("operation", help=f"Operation type — one of: {', '.join(sorted(VALID_OPS))}")
    p.add_argument("--title", required=True, help="Short title for the entry")
    p.add_argument("--created", default="", help="Comma-separated page paths created")
    p.add_argument("--updated", default="", help="Comma-separated page paths updated")
    p.add_argument("--contradictions", type=int, default=0, help="Number of contradictions detected")
    p.add_argument("--commit", default="", help="Short git commit hash (optional)")
    p.add_argument("--note", default="", help="Freeform note (optional)")
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    return p.parse_args(argv)


def format_list(raw: str) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_entry(args: argparse.Namespace) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"## [{now}] {args.operation} | {args.title}"]

    created = format_list(args.created)
    updated = format_list(args.updated)

    if created:
        lines.append(f"- Created: {', '.join(created)}")
    if updated:
        lines.append(f"- Updated: {', '.join(updated)}")
    if args.contradictions > 0:
        lines.append(f"- Contradictions: {args.contradictions}")
    if args.commit:
        lines.append(f"- Commit: {args.commit}")
    if args.note:
        lines.append(f"- Note: {args.note}")

    return "\n".join(lines) + "\n\n"


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.operation not in VALID_OPS:
        print(
            f"error: invalid operation '{args.operation}'. "
            f"Valid: {', '.join(sorted(VALID_OPS))}",
            file=sys.stderr,
        )
        return 2

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    log_path = wiki_root / "log.md"
    entry = build_entry(args)

    if not log_path.exists():
        print(f"warning: {log_path} not found, creating fresh log", file=sys.stderr)
        log_path.write_text(
            "# Wiki Log\n\n"
            "_Append-only chronological record. Every wiki operation adds an entry here._\n\n",
            encoding="utf-8",
        )

    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)

    print(f"appended entry to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
