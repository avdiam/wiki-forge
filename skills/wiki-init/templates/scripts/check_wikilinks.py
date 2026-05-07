#!/usr/bin/env python3
"""Pre-write wikilink validation — does each target slug resolve to a wiki page?

The wiki-ingestor agent uses this during step 2b/3 to verify wikilinks BEFORE
writing them. Pre-write validation eliminates the broken-link fix-up commit
class entirely (4 such fix-ups happened during Phase B of the inline-vs-fork
experiment).

Resolution rule (matches lint_wiki.py's check_broken_links):
- A target slug resolves if `<slug>.md` exists in any of the five page dirs:
  sources/, concepts/, entities/, comparisons/, syntheses/
- Aliases are NOT considered (lint doesn't either; the agent should write the
  canonical slug, not aliases)
- The `--planned` flag accepts slugs being created in the same ingest as
  resolvable (their .md files don't exist on disk yet)

Usage
-----

    # Positional slugs:
    python check_wikilinks.py <wiki> rag agent-skills google-adk

    # Bracketed wikilinks accepted; brackets are stripped:
    python check_wikilinks.py <wiki> '[[rag]]' '[[agent-skills]]'

    # Stdin (one slug per line, or whitespace-separated):
    echo 'rag agent-skills google-adk' | python check_wikilinks.py <wiki> --stdin

    # Planned slugs from this ingest (treated as resolvable):
    python check_wikilinks.py <wiki> --planned new-page-1,new-page-2 \\
        new-page-1 rag broken-target

    # JSON output (machine-readable):
    python check_wikilinks.py <wiki> --json rag broken-target

Exit codes
----------
- 0: every input slug resolves (existing OR planned)
- 1: at least one slug is broken
- 2: usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# Local copy so the script remains usable when invoked outside the wiki/.scripts
# package context (subagent fork may set CWD to wiki root, not wiki/.scripts).
PAGE_TYPES = ["sources", "concepts", "entities", "comparisons", "syntheses"]

# Strip [[...]] wrappers, anchors (#section), and aliases (|display) from inputs.
WIKILINK_RX = re.compile(r"^\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]$")


def normalize_target(raw: str) -> str:
    """Accept `slug`, `[[slug]]`, `[[slug#anchor]]`, `[[slug|alias]]` — return slug."""
    s = raw.strip()
    m = WIKILINK_RX.match(s)
    if m:
        return m.group(1).strip()
    # Bare slug — drop trailing/leading wikilink chars defensively.
    return s.lstrip("[").rstrip("]").strip()


def collect_existing_slugs(wiki_root: Path) -> set[str]:
    slugs: set[str] = set()
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if not type_dir.is_dir():
            continue
        for md in type_dir.glob("*.md"):
            slugs.add(md.stem)
    return slugs


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify wikilink target slugs resolve to existing wiki pages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("wiki_root", help="Path to the wiki root (contains CLAUDE.md)")
    p.add_argument(
        "targets",
        nargs="*",
        help="One or more target slugs to check (bare slug or [[slug]] form)",
    )
    p.add_argument(
        "--planned",
        default="",
        help="Comma-separated slugs being created in this ingest (treated as resolvable)",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read targets from stdin (whitespace-separated, in addition to positional)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON map {slug: ok|planned|broken} instead of human text",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    targets: list[str] = list(args.targets)
    if args.stdin:
        targets += sys.stdin.read().split()

    if not targets:
        print("error: no targets provided (positional or --stdin)", file=sys.stderr)
        return 2

    existing = collect_existing_slugs(wiki_root)
    planned = {s.strip() for s in args.planned.split(",") if s.strip()}

    result: dict[str, str] = {}
    for raw in targets:
        slug = normalize_target(raw)
        if not slug:
            continue
        if slug in existing:
            result[slug] = "ok"
        elif slug in planned:
            result[slug] = "planned"
        else:
            result[slug] = "broken"

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        # Group by status for human scan-ability.
        by_status: dict[str, list[str]] = {"ok": [], "planned": [], "broken": []}
        for slug, status in result.items():
            by_status[status].append(slug)
        for status in ("ok", "planned", "broken"):
            for slug in by_status[status]:
                print(f"{slug}: {status}")

    return 1 if any(s == "broken" for s in result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
