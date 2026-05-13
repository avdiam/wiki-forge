#!/usr/bin/env python3
"""List unresolved-link hubs — slugs with >=10 incoming wikilinks but no page.

Drives the `/wiki-stub` skill's hub-pattern path. Two modes:

    # Per-slug lookup — inbound count + top referring pages
    python list_unresolved_hubs.py <wiki> --slug <slug>

    # Project-wide scan — all slugs with >=N unresolved inbound, sorted desc
    python list_unresolved_hubs.py <wiki> --list [--threshold N]

V1 ships with its own grep-based count rather than extending graph_analyzer.py.
Refactor trigger documented in dev/archive/2026-05-13-wiki-stub-design.md: if a third caller of
per-slug inbound counts appears (e.g., v1.1 graduation hook, retrieval phase 4),
promote the count into graph_analyzer.py and update both callers.

Resolution rule matches check_wikilinks.py: a slug resolves if `<slug>.md`
exists in any of the five page-type dirs (sources/, concepts/, entities/,
comparisons/, syntheses/).

Output is JSON.

Exit codes
----------
- 0: ran successfully (regardless of count)
- 2: usage error or wiki-root not detected
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


PAGE_TYPES = ["sources", "concepts", "entities", "comparisons", "syntheses"]
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
DEFAULT_THRESHOLD = 10
TOP_REFERRERS = 5


def collect_existing_slugs(wiki_root: Path) -> set[str]:
    slugs: set[str] = set()
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if not type_dir.is_dir():
            continue
        for md in type_dir.glob("*.md"):
            slugs.add(md.stem)
    return slugs


def iter_pages(wiki_root: Path):
    """Yield (relative_path_str, body_text) for every page across the five dirs."""
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            yield md.relative_to(wiki_root).as_posix(), md.stem, text


def count_inbound_for_slug(wiki_root: Path, slug: str) -> tuple[int, list[str]]:
    """Return (total_inbound, top_referrers) for a single target slug.

    Self-references are excluded. Multiple links from the same page count once
    in the referrers list but each occurrence counts in the total.
    """
    total = 0
    by_page: Counter[str] = Counter()
    for rel_path, page_slug, body in iter_pages(wiki_root):
        if page_slug == slug:
            continue
        matches = WIKILINK_RE.findall(body)
        hits = sum(1 for m in matches if m.strip() == slug)
        if hits:
            total += hits
            by_page[rel_path] += hits
    top = [page for page, _ in by_page.most_common(TOP_REFERRERS)]
    return total, top


def scan_unresolved_hubs(wiki_root: Path, threshold: int) -> list[dict]:
    """Return all unresolved targets with inbound >= threshold, sorted desc."""
    existing = collect_existing_slugs(wiki_root)
    inbound: Counter[str] = Counter()
    referrers: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for rel_path, page_slug, body in iter_pages(wiki_root):
        for raw in WIKILINK_RE.findall(body):
            target = raw.strip()
            if not target or target == page_slug:
                continue
            inbound[target] += 1
            referrers[target][rel_path] += 1
    results: list[dict] = []
    for target, count in inbound.items():
        if target in existing:
            continue
        if count < threshold:
            continue
        results.append({
            "slug": target,
            "inbound": count,
            "top_referring_pages": [p for p, _ in referrers[target].most_common(TOP_REFERRERS)],
        })
    results.sort(key=lambda r: (-r["inbound"], r["slug"]))
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="List unresolved-link hubs in a wiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("wiki_root", help="Path to the wiki root (contains CLAUDE.md)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--slug", help="Single-slug mode: report inbound count + top referrers")
    mode.add_argument("--list", action="store_true", help="Project-wide mode: emit all unresolved hubs >= threshold")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help=f"Inbound threshold for --list mode (default: {DEFAULT_THRESHOLD})")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    if args.slug:
        slug = args.slug.strip().lstrip("[").rstrip("]").strip()
        resolves = slug in collect_existing_slugs(wiki_root)
        count, top = count_inbound_for_slug(wiki_root, slug)
        print(json.dumps({
            "slug": slug,
            "inbound": count,
            "referring_pages": top,
            "resolves": resolves,
        }, indent=2))
        return 0

    # --list mode
    results = scan_unresolved_hubs(wiki_root, args.threshold)
    print(json.dumps({
        "threshold": args.threshold,
        "count": len(results),
        "hubs": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
