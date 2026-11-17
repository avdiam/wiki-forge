#!/usr/bin/env python3
"""Scan vault for unprocessed files, pre-screen each, and output a triage report.

Usage:
    python triage_backlog.py --wiki <wiki-root> --vault <vault-root> [--json]

Outputs a JSON array of per-file records sorted by pre-screen verdict
(auto-skip first, then warn, then pass), each with frontmatter metadata
and pre-screen results. Designed to feed into the /wiki-triage skill
for LLM-assisted batch planning.

"Which files are pending" comes from the canonical matcher in
`source_backlog.compute_backlog` (content hash first, filename stem fallback),
the same one behind the welcome-report `Backlog:` count, so the two can't
disagree. A stem-only copy lived here until s147 and counted 7 renamed,
already-ingested clips as pending (26 vs 19).

Each record carries `canonical_slug`: the `YYYY-MM-DD-<kebab>` stem that
`normalize_vault.py` gives the file. Triage files list articles by this slug,
because `close_operation.py` ticks checkboxes by the ingest's canonical slug.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date
from pathlib import Path

# Force UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import parse_frontmatter
from pre_screen import pre_screen
from source_backlog import collect_source_hashes, compute_backlog
from normalize_vault import normalized_name


def find_existing_sources(wiki_root: Path) -> set[str]:
    """Return set of source slugs already in the wiki."""
    sources_dir = wiki_root / "pages" / "sources"
    if not sources_dir.is_dir():
        return set()
    return {f.stem for f in sources_dir.glob("*.md")}


def find_skipped(vault_root: Path) -> set[str]:
    """Return set of slugs already in vault/.skipped/."""
    skipped_dir = vault_root / ".skipped"
    if not skipped_dir.is_dir():
        return set()
    return {f.stem for f in skipped_dir.glob("*.md")}


def count_vault_files(vault_root: Path) -> int:
    """Content files the backlog matcher considers (same walk + exclusions)."""
    return len(compute_backlog(vault_root, set(), set()))


def triage(wiki_root: Path, vault_root: Path) -> dict:
    """Run full triage and return structured report."""
    handled_slugs = find_existing_sources(wiki_root) | find_skipped(vault_root)
    handled_hashes = collect_source_hashes(wiki_root / "pages" / "sources")
    pending = compute_backlog(vault_root, handled_slugs, handled_hashes)
    total = count_vault_files(vault_root)

    records = []
    counts = {"pass": 0, "warn": 0, "auto-skip": 0,
              "already-processed": total - len(pending)}

    for rel in pending:
        vf = vault_root / rel
        slug = vf.stem

        # Read frontmatter
        text = vf.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text)

        # Run pre-screen
        ps = pre_screen(wiki_root, vf, vault_root)
        verdict = ps["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1

        records.append({
            "file": str(vf.relative_to(vault_root)),
            "slug": slug,
            "canonical_slug": Path(normalized_name(vf)).stem,
            "title": fm.get("title", slug),
            "author": fm.get("author", None),
            "published": str(fm.get("published", "")),
            "tags": ps["source_tags"],
            "quality": fm.get("quality", None),
            "word_count": len(text.split()),
            "verdict": verdict,
            "reasons": ps["reasons"],
            "tag_overlap_count": ps["tag_overlap_count"],
            "tag_overlap": ps["tag_overlap"],
            "domain_score": ps["domain_score"],
            "promo_signals": ps["promo_signals"],
            "author_trust": ps.get("author_trust", "trusted"),
            "author_watchlist": ps.get("author_watchlist", None),
        })

    # Sort: auto-skip first, then warn, then pass
    verdict_order = {"auto-skip": 0, "warn": 1, "pass": 2}
    records.sort(key=lambda r: (verdict_order.get(r["verdict"], 3), r["slug"]))

    return {
        "date": date.today().isoformat(),
        "vault_files_total": total,
        "already_processed": counts["already-processed"],
        "pending": len(records),
        "counts": {
            "pass": counts["pass"],
            "warn": counts["warn"],
            "auto-skip": counts["auto-skip"],
        },
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description="Triage vault backlog for ingest.")
    parser.add_argument("--wiki", required=True, help="Path to wiki root")
    parser.add_argument("--vault", required=True, help="Path to vault root")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    wiki_root = Path(args.wiki).resolve()
    vault_root = Path(args.vault).resolve()

    report = triage(wiki_root, vault_root)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Triage report — {report['date']}")
        print(f"Vault files: {report['vault_files_total']} total, "
              f"{report['already_processed']} already processed, "
              f"{report['pending']} pending")
        print(f"Verdicts: {report['counts']['pass']} pass, "
              f"{report['counts']['warn']} warn, "
              f"{report['counts']['auto-skip']} auto-skip")
        print()
        for r in report["records"]:
            marker = {"pass": "+", "warn": "?", "auto-skip": "x"}[r["verdict"]]
            print(f"  [{marker}] {r['slug']}")
            print(f"      {r['title']} ({r['author'] or 'unknown'})")
            print(f"      tags: {', '.join(r['tags']) or '(none)'} | "
                  f"overlap: {r['tag_overlap_count']} | "
                  f"domain: {r['domain_score']:.0%} | "
                  f"promo: {r['promo_signals']}")
            if r["verdict"] != "pass":
                for reason in r["reasons"]:
                    print(f"      ! {reason}")
            print()


if __name__ == "__main__":
    main()
