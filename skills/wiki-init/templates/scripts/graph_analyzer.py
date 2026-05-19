#!/usr/bin/env python3
"""Analyze the wikilink graph of an LLM Wiki vault.

Reports hubs, orphans, sinks, bridges, and connected components so the
wiki-linter agent knows where to focus cross-referencing work.

Pure stdlib — no external dependencies.

Usage:
    python graph_analyzer.py <wiki-root>
    python graph_analyzer.py <wiki-root> --json
    python graph_analyzer.py <wiki-root> --top 20
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from constants import PAGE_TYPES
from frontmatter import parse_frontmatter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
TYPED_EDGE_FIELDS = ("contradicts", "supersedes", "superseded_by")
TYPED_LINK_RE = re.compile(r"^\[\[([^\]|#]+?)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]$")


def build_graph(
    wiki_root: Path,
    *,
    include_typed_edges: bool = False,
) -> tuple[set[str], dict[str, set[str]], dict[str, set[str]]]:
    """Build the wikilink graph from type directories under wiki root.

    Returns (nodes, outbound_edges, inbound_edges) where nodes are slugs
    and edges map slug → set of slugs.

    When `include_typed_edges=True`, frontmatter typed-edge declarations
    (`contradicts:` / `supersedes:` / `superseded_by:`) are folded into
    outbound/inbound alongside body wikilinks (untyped fold-in — edge
    types are not preserved in the returned shape). Default `False`
    preserves the wikilink-only semantics whole-graph analysis depends on
    (`analyze()`, the welcome-report drill, lint Pass A). The flag is
    forward-insurance for Tier-3 graph-traversal extensions where typed-
    edge reachability matters; flipping it lets both `graph_analyzer.py`
    callers and `graph_query.py` benefit at the same time without
    re-architecting.
    """
    # Collect all page slugs and their paths (type dirs directly under wiki root)
    nodes: set[str] = set()
    slug_to_path: dict[str, str] = {}

    for page_type in PAGE_TYPES:
        type_dir = wiki_root / "pages" / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            slug = md.stem
            nodes.add(slug)
            slug_to_path[slug] = md.relative_to(wiki_root).as_posix()

    # Build edges from wikilinks in page bodies
    outbound: dict[str, set[str]] = defaultdict(set)
    inbound: dict[str, set[str]] = defaultdict(set)

    for page_type in PAGE_TYPES:
        type_dir = wiki_root / "pages" / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            slug = md.stem
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in WIKILINK_RE.finditer(text):
                target = m.group(1).strip()
                # Resolve: strip .md extension if present
                if target.endswith(".md"):
                    target = target[:-3]
                # Only count edges to pages that exist
                if target in nodes and target != slug:
                    outbound[slug].add(target)
                    inbound[target].add(slug)
            if include_typed_edges:
                try:
                    fm = parse_frontmatter(text)
                except Exception:
                    fm = {}
                for field in TYPED_EDGE_FIELDS:
                    value = fm.get(field)
                    if not value:
                        continue
                    candidates = [value] if isinstance(value, str) else value
                    if not isinstance(candidates, list):
                        continue
                    for c in candidates:
                        if not isinstance(c, str):
                            continue
                        match = TYPED_LINK_RE.match(c.strip())
                        if not match:
                            continue
                        target = match.group(1).strip()
                        if target.endswith(".md"):
                            target = target[:-3]
                        if target in nodes and target != slug:
                            outbound[slug].add(target)
                            inbound[target].add(slug)

    return nodes, dict(outbound), dict(inbound)


def count_typed_edges(wiki_root: Path) -> dict[str, int]:
    """Count typed-edge declarations across all pages by field.

    Reads each page's frontmatter and tallies how many wikilinks appear in
    the `contradicts:`, `supersedes:`, and `superseded_by:` fields. Each
    edge is counted on both sides for symmetric fields — `contradicts: [B]`
    on page A contributes 1 to the contradicts count; the inverse on B
    contributes another 1, so a single symmetric pair shows as 2.

    Returns `{"contradicts": N, "supersedes": N, "superseded_by": N}`.
    """
    counts: dict[str, int] = {field: 0 for field in TYPED_EDGE_FIELDS}
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / "pages" / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            try:
                fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            for field in TYPED_EDGE_FIELDS:
                value = fm.get(field)
                if not value:
                    continue
                candidates = [value] if isinstance(value, str) else value
                if not isinstance(candidates, list):
                    continue
                for c in candidates:
                    if isinstance(c, str) and TYPED_LINK_RE.match(c.strip()):
                        counts[field] += 1
    return counts


def find_components(
    nodes: set[str],
    outbound: dict[str, set[str]],
    inbound: dict[str, set[str]],
) -> list[set[str]]:
    """Find connected components (treating edges as undirected)."""
    adj: dict[str, set[str]] = defaultdict(set)
    for n in nodes:
        adj[n] |= outbound.get(n, set())
        adj[n] |= inbound.get(n, set())

    seen: set[str] = set()
    components: list[set[str]] = []
    for n in nodes:
        if n in seen:
            continue
        stack = [n]
        comp: set[str] = set()
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            comp.add(v)
            stack.extend(adj[v] - seen)
        components.append(comp)

    components.sort(key=len, reverse=True)
    return components


def analyze(wiki_root: Path, top: int = 10) -> dict:
    """Run full graph analysis. Returns JSON-serializable report."""
    nodes, outbound, inbound = build_graph(wiki_root)

    if not nodes:
        return {
            "total_pages": 0,
            "total_edges": 0,
            "avg_outbound": 0,
            "avg_inbound": 0,
            "top_hubs_outbound": [],
            "top_hubs_inbound": [],
            "orphans": [],
            "sinks": [],
            "components": [],
            "component_count": 0,
            "typed_edges": {field: 0 for field in TYPED_EDGE_FIELDS},
        }

    total_edges = sum(len(v) for v in outbound.values())

    # Top hubs by outbound links (pages that reference the most others)
    hubs_out = sorted(nodes, key=lambda n: len(outbound.get(n, set())), reverse=True)[:top]
    # Top hubs by inbound links (most-referenced pages)
    hubs_in = sorted(nodes, key=lambda n: len(inbound.get(n, set())), reverse=True)[:top]
    # Orphans: no inbound links (nobody references them)
    orphans = sorted(n for n in nodes if not inbound.get(n))
    # Sinks: no outbound links (dead-end pages)
    sinks = sorted(n for n in nodes if not outbound.get(n))
    # Connected components
    components = find_components(nodes, outbound, inbound)

    return {
        "total_pages": len(nodes),
        "total_edges": total_edges,
        "avg_outbound": round(total_edges / len(nodes), 1) if nodes else 0,
        "avg_inbound": round(total_edges / len(nodes), 1) if nodes else 0,
        "top_hubs_outbound": [
            {"page": h, "outbound": len(outbound.get(h, set()))} for h in hubs_out
        ],
        "top_hubs_inbound": [
            {"page": h, "inbound": len(inbound.get(h, set()))} for h in hubs_in
        ],
        "orphans": orphans,
        "sinks": sinks,
        "components": [
            {"size": len(c), "sample": sorted(c)[:5]} for c in components[:10]
        ],
        "component_count": len(components),
        "typed_edges": count_typed_edges(wiki_root),
    }


def render_human(report: dict) -> str:
    """Format the report for human reading."""
    lines = [
        f"Wiki graph — {report['total_pages']} pages, {report['total_edges']} edges",
        f"Average outbound links per page: {report['avg_outbound']}",
        f"Connected components: {report['component_count']}",
        "",
    ]

    if report["top_hubs_outbound"]:
        lines.append("Top outbound hubs (pages that link to many others):")
        for h in report["top_hubs_outbound"]:
            if h["outbound"] > 0:
                lines.append(f"  {h['page']}  ({h['outbound']} out)")
        lines.append("")

    if report["top_hubs_inbound"]:
        lines.append("Top inbound hubs (most-referenced pages):")
        for h in report["top_hubs_inbound"]:
            if h["inbound"] > 0:
                lines.append(f"  {h['page']}  ({h['inbound']} in)")
        lines.append("")

    lines.append(f"Orphans (no inbound links): {len(report['orphans'])}")
    for o in report["orphans"][:15]:
        lines.append(f"  - {o}")
    lines.append("")

    lines.append(f"Sinks (no outbound links): {len(report['sinks'])}")
    for s in report["sinks"][:15]:
        lines.append(f"  - {s}")
    lines.append("")

    if report["component_count"] > 1:
        lines.append(f"Disconnected clusters ({report['component_count']} components):")
        for i, c in enumerate(report["components"][:5]):
            lines.append(f"  Component {i + 1}: {c['size']} pages — {', '.join(c['sample'])}")
        lines.append("")

    typed = report.get("typed_edges") or {}
    typed_total = sum(typed.values())
    if typed_total > 0:
        lines.append(
            f"Typed edges: {typed.get('contradicts', 0)} contradicts, "
            f"{typed.get('supersedes', 0)} supersedes, "
            f"{typed.get('superseded_by', 0)} superseded_by"
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze the wikilink graph of an LLM Wiki vault"
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument("--top", type=int, default=10, help="Number of top hubs to show")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    report = analyze(wiki_root, args.top)

    if args.json:
        print(json.dumps(report, indent=2, default=list))
    else:
        print(render_human(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
