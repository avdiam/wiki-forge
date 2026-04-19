#!/usr/bin/env python3
"""Mechanical lint checks for the wiki.

Fast deterministic checks only. Semantic checks (unacknowledged
contradictions, category drift, stale facts) are left to the wiki-linter
subagent, which reads flagged pages with LLM reasoning.

Checks:
    - orphans              pages with no inbound [[wikilinks]]
    - broken_links         wikilinks pointing at pages that don't exist
    - missing_frontmatter  pages missing required fields
    - stale                stale:true OR last_updated > 90 days ago
    - low_confidence       confidence < 0.5
    - index_drift          page dirs vs indexes/index-*.md mismatch
    - source_backlog       vault/ files with no matching sources/ page
    - broken_sources       sources: frontmatter entries pointing to missing vault files

Usage:
    python lint_wiki.py <wiki-root> [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from frontmatter import parse_frontmatter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


PAGE_TYPES = ["sources", "concepts", "entities", "comparisons", "syntheses"]
REQUIRED_FIELDS = ["title", "type", "category", "summary", "confidence", "sources", "last_updated"]
STALE_DAYS = 90
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


def collect_pages(wiki_root: Path) -> dict:
    """Collect all pages from type directories directly under wiki root."""
    pages: dict = {}
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            pages[md.stem] = {
                "path": md.relative_to(wiki_root).as_posix(),
                "type": page_type,
                "frontmatter": parse_frontmatter(text),
                "body": text,
            }
    return pages


def find_wikilinks(text: str) -> list[str]:
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]


def check_orphans(pages: dict) -> list[str]:
    referenced: set[str] = set()
    for info in pages.values():
        for link in find_wikilinks(info["body"]):
            referenced.add(link)
    return sorted(slug for slug in pages if slug not in referenced)


def check_broken_links(pages: dict) -> list[dict]:
    broken: list[dict] = []
    slugs = set(pages.keys())
    for slug, info in pages.items():
        for link in find_wikilinks(info["body"]):
            if link not in slugs:
                broken.append({"page": info["path"], "target": link})
    return broken


def check_missing_frontmatter(pages: dict) -> list[dict]:
    issues: list[dict] = []
    for slug, info in pages.items():
        fm = info["frontmatter"]
        missing = [f for f in REQUIRED_FIELDS if not fm.get(f) and fm.get(f) != 0]
        if missing:
            issues.append({"page": info["path"], "missing": missing})
    return issues


def check_stale(pages: dict) -> list[dict]:
    issues: list[dict] = []
    cutoff = date.today() - timedelta(days=STALE_DAYS)
    for slug, info in pages.items():
        fm = info["frontmatter"]
        if fm.get("stale") is True:
            issues.append({"page": info["path"], "reason": "stale: true in frontmatter"})
            continue
        last = fm.get("last_updated")
        if isinstance(last, str):
            try:
                dt = datetime.strptime(last, "%Y-%m-%d").date()
                if dt < cutoff:
                    issues.append({"page": info["path"], "reason": f"last_updated {last} (> {STALE_DAYS} days ago)"})
            except ValueError:
                pass
    return issues


def check_low_confidence(pages: dict) -> list[dict]:
    issues: list[dict] = []
    for slug, info in pages.items():
        conf = info["frontmatter"].get("confidence")
        if isinstance(conf, (int, float)):
            if conf < 0.0 or conf > 1.0:
                issues.append({"page": info["path"], "confidence": conf, "reason": "out of range (must be 0.0-1.0)"})
            elif conf < 0.5:
                issues.append({"page": info["path"], "confidence": conf})
    return issues


def check_index_drift(wiki_root: Path, pages: dict) -> dict:
    """Check for drift between page dirs and indexes/index-*.md files."""
    indexes_dir = wiki_root / "indexes"
    index_text = ""
    found_indices = False
    if not indexes_dir.is_dir():
        return {"missing_index": True, "in_pages_not_index": [], "in_index_not_pages": []}
    for idx_file in sorted(indexes_dir.glob("index-*.md")):
        found_indices = True
        try:
            index_text += idx_file.read_text(encoding="utf-8") + "\n"
        except OSError:
            continue

    if not found_indices:
        return {"missing_index": True, "in_pages_not_index": [], "in_index_not_pages": []}

    index_slugs = set(find_wikilinks(index_text))
    page_slugs = set(pages.keys())
    return {
        "missing_index": False,
        "in_pages_not_index": sorted(page_slugs - index_slugs),
        "in_index_not_pages": sorted(index_slugs - page_slugs),
    }


def check_source_backlog(vault_dir: Path, pages: dict) -> list[str]:
    """Check for raw files in vault/ that have no corresponding source summary."""
    if not vault_dir.is_dir():
        return []
    source_slugs = {
        slug for slug, info in pages.items() if info["type"] == "sources"
    }
    backlog: list[str] = []
    for raw_file in sorted(vault_dir.glob("*.md")):
        if raw_file.stem not in source_slugs:
            backlog.append(raw_file.name)
    return backlog


def check_broken_sources(vault_dir: Path, pages: dict) -> list[dict]:
    """Check that sources: frontmatter entries point to existing vault/ files."""
    issues: list[dict] = []
    for slug, info in pages.items():
        fm = info["frontmatter"]
        sources = fm.get("sources") or []
        if not isinstance(sources, list):
            continue
        for src in sources:
            if isinstance(src, str):
                # sources field contains paths like "vault/2026-04-15-article.md"
                # or just the filename — check both
                src_path = vault_dir / Path(src).name
                if not src_path.is_file():
                    issues.append({"page": info["path"], "missing_source": src})
    return issues


def _find_vault_dir(wiki_root: Path) -> Path:
    """Find the vault/ directory (sibling to wiki/)."""
    return wiki_root.parent / "vault"


def build_report(wiki_root: Path, vault_dir: Path | None = None) -> dict:
    if vault_dir is None:
        vault_dir = _find_vault_dir(wiki_root)
    pages = collect_pages(wiki_root)
    return {
        "wiki_root": str(wiki_root),
        "vault_dir": str(vault_dir),
        "total_pages": len(pages),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "orphans": check_orphans(pages),
        "broken_links": check_broken_links(pages),
        "missing_frontmatter": check_missing_frontmatter(pages),
        "stale": check_stale(pages),
        "low_confidence": check_low_confidence(pages),
        "index_drift": check_index_drift(wiki_root, pages),
        "source_backlog": check_source_backlog(vault_dir, pages),
        "broken_sources": check_broken_sources(vault_dir, pages),
    }


def render_human(report: dict) -> str:
    lines = [
        f"Wiki lint — {report['wiki_root']}",
        f"Checked at: {report['checked_at']}",
        f"Total pages: {report['total_pages']}",
        "",
        f"Orphans:              {len(report['orphans'])}",
        f"Broken links:         {len(report['broken_links'])}",
        f"Missing frontmatter:  {len(report['missing_frontmatter'])}",
        f"Stale:                {len(report['stale'])}",
        f"Low confidence:       {len(report['low_confidence'])}",
        f"Source backlog:       {len(report['source_backlog'])}",
        f"Broken sources:      {len(report['broken_sources'])}",
    ]
    drift = report["index_drift"]
    if drift.get("missing_index"):
        lines.append("Index:                MISSING (no index-*.md files found)")
    else:
        drift_count = len(drift["in_pages_not_index"]) + len(drift["in_index_not_pages"])
        lines.append(f"Index drift:          {drift_count}")

    if report["broken_links"]:
        lines.append("")
        lines.append("Broken links:")
        for item in report["broken_links"][:20]:
            lines.append(f"  {item['page']} \u2192 [[{item['target']}]]")

    if report["missing_frontmatter"]:
        lines.append("")
        lines.append("Missing frontmatter fields:")
        for item in report["missing_frontmatter"][:20]:
            lines.append(f"  {item['page']}: {', '.join(item['missing'])}")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Mechanical lint for the wiki")
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument("--vault", default=None, help="Path to the vault/ directory (default: sibling of wiki root)")
    parser.add_argument("--json", action="store_true", help="Emit JSON (for subagent consumption)")
    args = parser.parse_args(argv)

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    vault_dir = Path(args.vault).resolve() if args.vault else None
    report = build_report(wiki_root, vault_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
