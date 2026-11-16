#!/usr/bin/env python3
"""Enumerate `verify: true` pages with bucket classification + callout text.

Read-only scan. Glob wiki/{concepts,entities,comparisons,syntheses}/*.md;
filter to pages whose frontmatter has `verify: true` (mirrors lint_wiki.py's
`check_needs_verification`); extract the inline `⚠ needs verification:`
callout text; classify each into a remediation bucket per the
2026-05-08 queue audit:

    A — pure bio thinness          (entity + category=ai-practitioners)
    B — entity with specific claims (entity + other category)
    C — concept/comparison/synthesis

Source pages (Bucket D in the audit) are out of scope by glob — different
remediation shape (track via check_source_freshness.py / accept upstream
gap), not the kind of corroborating-source search /wiki-corroborate runs.

Output is JSON on stdout — list of objects, one per flag, sorted by
`last_updated` ascending (least-recently-touched first, used as a proxy
for "oldest-flagged"). The shape:

    [
      {
        "page_slug": "kimi-k2-6",
        "page_path": "wiki/pages/entities/kimi-k2-6.md",
        "page_type": "entity",
        "page_bucket": "B",
        "callout_text": "the Muon optimizer adoption ...",
        "sources_count": 2,
        "confidence": 0.7,
        "last_updated": "2026-04-29"
      },
      ...
    ]

Used by `/wiki-corroborate` to drive its per-flag WebSearch loop.

Age filter (`--older-than N`): keeps flags whose page `last_updated` is at
least N days before `--as-of` (default: today). There is no flag-set date in
frontmatter, so age is measured from the page's last touch: a flag is set at
or before that touch, which makes `today - last_updated` a LOWER bound on the
flag's real age. Every flag `--older-than N` returns has truly waited >= N
days; a page edited recently without clearing its flag reads as young. Pages
with no parseable `last_updated` are never counted as aged.

`verify_queue_summary()` is the same scan reduced to counts, for the wake-up
drill (`drill_state.py` → `run_drill.py` `verify_queue` field).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from frontmatter import parse_frontmatter  # noqa: E402

# Page types in scope. Source pages excluded — Bucket D in design § 2.
SCOPED_DIRS = ("concepts", "entities", "comparisons", "syntheses")
PRACTITIONER_CATEGORY = "ai-practitioners"

# Callout pattern: "needs verification: <text...>" preceded by an optional
# warning prefix. Tolerant of three observed shapes:
#   ⚠ needs verification: ...                  (schema-canonical)
#   > [!WARNING] needs verification: ...       (Obsidian callout block)
#   > needs verification: ...                  (plain blockquote variant)
# Spans from the marker through the first blank line, next-block marker,
# or end of file. Continuation lines (typical author-wrap) are absorbed.
_CALLOUT_RE = re.compile(
    r"(?:⚠\s*|>\s*(?:\[![A-Z]+\]\s*)?)?needs verification:\s*"
    r"([^\n]*(?:\n(?!\s*\n|\s*#|\s*-{3,}|\s*```)[^\n]*)*)",
    re.MULTILINE,
)


def _slug_from_path(path: Path, wiki_root: Path) -> str:
    return path.stem


def _bucket(page_type: str, category: str | None) -> str:
    if page_type in {"concept", "comparison", "synthesis"}:
        return "C"
    if page_type == "entity":
        if (category or "").strip().lower() == PRACTITIONER_CATEGORY:
            return "A"
        return "B"
    # Defensive — should not happen given SCOPED_DIRS filter
    return "B"


def _extract_callout(body: str) -> str | None:
    m = _CALLOUT_RE.search(body)
    if not m:
        return None
    text = m.group(1).strip()
    # Collapse internal whitespace runs (line wraps -> single spaces) for
    # cleaner downstream prompt embedding without losing semantic content.
    return re.sub(r"\s+", " ", text)


def _read_pages(wiki_root: Path) -> list[dict]:
    """Walk SCOPED_DIRS and return one dict per `verify: true` page."""
    out: list[dict] = []
    for sub in SCOPED_DIRS:
        d = wiki_root / "pages" / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm.get("verify") is not True:
                continue
            # Body is everything after the closing --- of the frontmatter
            # block. parse_frontmatter only returns the dict so we re-locate.
            body = text
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    body = text[end + 4 :]
            sources = fm.get("sources") or []
            row = {
                "page_slug": _slug_from_path(p, wiki_root),
                "page_path": p.relative_to(wiki_root.parent).as_posix(),
                "page_type": fm.get("type") or sub.rstrip("s"),
                "page_bucket": _bucket(fm.get("type") or sub.rstrip("s"), fm.get("category")),
                "callout_text": _extract_callout(body),
                "sources_count": len(sources) if isinstance(sources, list) else 0,
                "confidence": fm.get("confidence"),
                "last_updated": fm.get("last_updated"),
            }
            out.append(row)
    out.sort(key=lambda r: (r.get("last_updated") or "9999-99-99", r["page_slug"]))
    return out


def age_days(last_updated, as_of: date) -> int | None:
    """Days from `last_updated` (ISO date or date object) to `as_of`; None if unparseable."""
    if last_updated is None:
        return None
    if isinstance(last_updated, date):
        d = last_updated
    else:
        try:
            d = date.fromisoformat(str(last_updated).strip()[:10])
        except ValueError:
            return None
    return (as_of - d).days


def filter_older_than(pages: list[dict], days: int, as_of: date) -> list[dict]:
    """Flags whose age (from `last_updated`, a lower bound) is >= `days`."""
    out = []
    for p in pages:
        a = age_days(p.get("last_updated"), as_of)
        if a is not None and a >= days:
            out.append(p)
    return out


def verify_queue_summary(wiki_root: Path, older_than: int = 60, as_of: date | None = None) -> dict:
    """Counts-only view of the queue for the wake-up drill.

    {"total", "aged", "older_than_days", "oldest_days", "oldest_slug",
     "aged_buckets": {"A","B","C"}}. `aged` uses the lower-bound age (see module doc).
    """
    as_of = as_of or date.today()
    pages = _read_pages(wiki_root)
    aged = filter_older_than(pages, older_than, as_of)
    ages = [(age_days(p.get("last_updated"), as_of), p["page_slug"]) for p in pages]
    ages = [x for x in ages if x[0] is not None]
    oldest = max(ages) if ages else (None, None)
    return {
        "total": len(pages),
        "aged": len(aged),
        "older_than_days": older_than,
        "oldest_days": oldest[0],
        "oldest_slug": oldest[1],
        "aged_buckets": {b: sum(1 for r in aged if r["page_bucket"] == b) for b in ("A", "B", "C")},
    }


def _apply_filters(pages: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.single:
        target = args.single.strip().strip("[").strip("]").removesuffix(".md")
        return [p for p in pages if p["page_slug"] == target]
    if args.batch:
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", args.batch)
        if not m:
            raise SystemExit(f"error: --batch must be 'X-Y' (got: {args.batch!r})")
        start, end = int(m.group(1)), int(m.group(2))
        if start < 1 or end < start:
            raise SystemExit(f"error: --batch range invalid (got: {args.batch!r})")
        return pages[start - 1 : end]
    if args.all:
        return pages
    # Default: first 10
    return pages[:10]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    p.add_argument("--wiki", default="wiki", help="Path to the wiki root (default: wiki)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--single", help="Single slug (bare, [[wikilink]], or <slug>.md form)")
    g.add_argument("--batch", help="Range 'X-Y' (1-indexed inclusive)")
    g.add_argument("--all", action="store_true", help="Emit all flags")
    p.add_argument("--total-only", action="store_true",
                   help="Print just the total count to stdout (for cheap counting)")
    p.add_argument("--older-than", type=int, metavar="N",
                   help="Keep only flags whose page last_updated is >= N days old "
                        "(a lower bound on flag age). Applied before --single/--batch/--all; "
                        "`total` then counts the aged subset.")
    p.add_argument("--as-of", metavar="YYYY-MM-DD",
                   help="Reference date for --older-than (default: today)")
    args = p.parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2
    if args.older_than is not None and args.older_than < 0:
        raise SystemExit(f"error: --older-than must be >= 0 (got: {args.older_than})")
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError:
        raise SystemExit(f"error: --as-of must be YYYY-MM-DD (got: {args.as_of!r})")

    pages = _read_pages(wiki_root)
    queue_total = len(pages)
    if args.older_than is not None:
        pages = filter_older_than(pages, args.older_than, as_of)
    if args.total_only:
        print(len(pages))
        return 0

    selected = _apply_filters(pages, args)
    out = {
        "total": len(pages),
        "returned": len(selected),
        "buckets_total": {b: sum(1 for r in pages if r["page_bucket"] == b) for b in ("A", "B", "C")},
        "flags": selected,
    }
    if args.older_than is not None:
        out["older_than_days"] = args.older_than
        out["as_of"] = as_of.isoformat()
        out["queue_total"] = queue_total
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
