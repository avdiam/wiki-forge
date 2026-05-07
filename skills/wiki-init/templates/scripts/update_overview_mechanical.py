#!/usr/bin/env python3
"""Mechanically refresh wiki/overview.md without LLM input.

Updates only the bookkeeping parts of the overview — the per-type page
counts, top-hub list, graph stats, and a "last refreshed" stamp. The
narrative sections (cluster map, major themes, current debates,
interesting connections, unresolved questions, recent ingests) are left
untouched.

Designed for the Mode A path of /wiki-overview: cheap, idempotent,
deterministic, runs in milliseconds. The "Recent ingests N–M" section
update is the LLM-bounded responsibility of wiki-curator (mode: soft) —
this script does not touch it.

Pure stdlib + invokes graph_analyzer.py via subprocess.

Usage:
    python update_overview_mechanical.py <wiki-root>
    python update_overview_mechanical.py <wiki-root> --dry-run
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from datetime import date
from pathlib import Path

# Local imports from sibling .scripts modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import PAGE_TYPES  # noqa: E402
from graph_analyzer import analyze as analyze_graph  # noqa: E402
from log_parsers import count_ingests  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

REFRESH_MARKER_RE = re.compile(
    r"^_Mechanical refresh at ingest #\d+ \([0-9-]+\)[^_\n]*_\s*$",
    re.MULTILINE,
)


def count_pages_by_type(wiki_root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in PAGE_TYPES:
        d = wiki_root / t
        counts[t] = len(list(d.glob("*.md"))) if d.is_dir() else 0
    return counts


def build_stats_section(graph: dict, type_counts: dict[str, int]) -> str:
    """Return the body for the ## Stats section (without the header)."""
    total = graph["total_pages"]
    hubs = graph["top_hubs_inbound"][:6]
    hub_str = ", ".join(f"{h['page']} ({h['inbound']})" for h in hubs if h["inbound"] > 0)
    orphans = len(graph.get("orphans", []))
    comps = graph.get("component_count", 1)
    typed = graph.get("typed_edges", {})
    contradicts = typed.get("contradicts", 0)
    supersedes = typed.get("supersedes", 0)
    superseded_by = typed.get("superseded_by", 0)

    type_breakdown = " / ".join(
        f"{type_counts.get(t, 0)} {t}" for t in PAGE_TYPES
    )

    lines = [
        f"- Total pages: **{total}** ({type_breakdown})",
        f"- Top hubs by inbound wikilinks: {hub_str}",
        (
            f"- Graph: {orphans} orphan{'s' if orphans != 1 else ''}, "
            f"{comps} connected component{'s' if comps != 1 else ''}, "
            f"{contradicts} contradiction edge{'s' if contradicts != 1 else ''}, "
            f"{supersedes} supersession edge{'s' if supersedes != 1 else ''}"
            + (f" ({superseded_by} superseded_by)" if superseded_by else "")
        ),
        "",
        "(Stats regenerated mechanically from current graph state. For exact per-type counts see `indexes/index.md`.)",
    ]
    return "\n".join(lines) + "\n"


def replace_section(text: str, header: str, new_body: str) -> str:
    """Replace the body between `header` and the next ##-level header (or EOF).

    The header itself is preserved; only its body is replaced. If the
    header doesn't exist, the body is appended at end of file under a
    new header.
    """
    pattern = re.compile(
        r"(^" + re.escape(header) + r"\s*$\n)(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub(lambda m: m.group(1) + new_body, text)
    # Append at end if missing
    sep = "" if text.endswith("\n") else "\n"
    return text + f"{sep}\n{header}\n\n{new_body}"


def upsert_refresh_marker(text: str, marker: str) -> str:
    """Replace the existing mechanical-refresh marker line, or insert one
    immediately after the first paragraph following the # Overview heading.
    """
    if REFRESH_MARKER_RE.search(text):
        return REFRESH_MARKER_RE.sub(marker, text, count=1)

    # Insert after the first italic intro paragraph under "# Overview"
    intro_pattern = re.compile(
        r"(^# Overview\s*\n\s*\n_[^_]+_\s*\n)",
        re.MULTILINE,
    )
    m = intro_pattern.search(text)
    if m:
        insert_at = m.end()
        return text[:insert_at] + "\n" + marker + "\n" + text[insert_at:]

    # Fallback: prepend after any leading blank
    return marker + "\n\n" + text


def refresh(wiki_root: Path, dry_run: bool = False) -> int:
    overview_path = wiki_root / "overview.md"
    if not overview_path.is_file():
        print(f"error: {overview_path} not found", file=sys.stderr)
        return 2

    original = overview_path.read_text(encoding="utf-8")

    graph = analyze_graph(wiki_root, top=10)
    type_counts = count_pages_by_type(wiki_root)
    ingest_n = count_ingests(wiki_root)
    today = date.today().isoformat()

    stats_body = build_stats_section(graph, type_counts)
    marker = (
        f"_Mechanical refresh at ingest #{ingest_n} ({today}) — "
        f"page counts, hub list, and stats regenerated; narrative sections untouched._"
    )

    new_text = original
    new_text = replace_section(new_text, "## Stats", stats_body)
    new_text = upsert_refresh_marker(new_text, marker)

    if new_text == original:
        print(f"overview.md unchanged (already current at ingest #{ingest_n})")
        return 0

    if dry_run:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile="overview.md",
            tofile="overview.md (proposed)",
            n=3,
        )
        sys.stdout.writelines(diff)
        return 0

    overview_path.write_text(new_text, encoding="utf-8")
    print(f"overview.md refreshed (ingest #{ingest_n}, {today})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mechanically refresh wiki/overview.md (Mode A bookkeeping)."
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing.",
    )
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(
            f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)",
            file=sys.stderr,
        )
        return 2

    return refresh(wiki_root, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
