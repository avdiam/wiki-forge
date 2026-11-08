#!/usr/bin/env python3
"""Path queries over the wikilink graph.

Exposes three traversal primitives as subcommands. Walks the wikilink
graph (undirected: inbound + outbound treated as one edge per pair).
Imports build_graph from graph_analyzer; shares the same parser so any
build_graph improvement (e.g., the future `include_typed_edges` flag for
Tier-3 typed traversal) propagates here for free.

Pure stdlib — no external dependencies.

Usage:
    python graph_query.py <wiki-root> path <slug-a> <slug-b> [--json]
    python graph_query.py <wiki-root> neighbors <slug> [--depth N] [--json]
    python graph_query.py <wiki-root> subgraph <slug> [--json]

Exit codes:
    0  ok (result printed; may be empty for disconnected/no-neighbors cases)
    2  invalid args / unknown slug / wiki root not found
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

from graph_analyzer import build_graph

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _undirected_adjacency(
    nodes: set[str],
    outbound: dict[str, set[str]],
    inbound: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Fold inbound + outbound into a single undirected adjacency map."""
    adj: dict[str, set[str]] = defaultdict(set)
    for n in nodes:
        adj[n] |= outbound.get(n, set())
        adj[n] |= inbound.get(n, set())
    return adj


def shortest_path(
    adj: dict[str, set[str]],
    start: str,
    end: str,
) -> list[str] | None:
    """BFS shortest path from start to end. Undirected.

    Returns the slug sequence including endpoints, or None if disconnected.
    Length 1 ([start]) when start == end.
    """
    if start == end:
        return [start]
    visited: dict[str, str | None] = {start: None}
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in sorted(adj.get(node, set())):
            if neighbor in visited:
                continue
            visited[neighbor] = node
            if neighbor == end:
                # reconstruct
                path: list[str] = [end]
                cur: str | None = node
                while cur is not None:
                    path.append(cur)
                    cur = visited[cur]
                path.reverse()
                return path
            queue.append(neighbor)
    return None


def neighbors_within(
    adj: dict[str, set[str]],
    start: str,
    depth: int,
) -> list[tuple[str, int]]:
    """BFS from start; return (slug, hop_distance) for nodes within depth.

    Excludes start itself. Sorted by (depth asc, slug asc).
    """
    if depth < 1:
        return []
    distances: dict[str, int] = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        d = distances[node]
        if d >= depth:
            continue
        for neighbor in adj.get(node, set()):
            if neighbor in distances:
                continue
            distances[neighbor] = d + 1
            queue.append(neighbor)
    result = [(slug, d) for slug, d in distances.items() if slug != start]
    result.sort(key=lambda pair: (pair[1], pair[0]))
    return result


def subgraph_around(
    adj: dict[str, set[str]],
    start: str,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Return the connected-component subgraph containing start.

    Returns (sorted_slugs, sorted_edges) where each edge is (a, b) with a < b
    to avoid double-counting in undirected representation.
    """
    component: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        if node in component:
            continue
        component.add(node)
        for neighbor in adj.get(node, set()):
            if neighbor not in component:
                queue.append(neighbor)
    edges: set[tuple[str, str]] = set()
    for node in component:
        for neighbor in adj.get(node, set()):
            if neighbor in component:
                a, b = sorted((node, neighbor))
                edges.add((a, b))
    return sorted(component), sorted(edges)


def _resolve_slug(slug: str, nodes: set[str]) -> str:
    """Strip a `[[…]]` or trailing `.md` if present; return as-is otherwise."""
    s = slug.strip()
    if s.startswith("[[") and s.endswith("]]"):
        s = s[2:-2]
    if s.endswith(".md"):
        s = s[:-3]
    return s


def _check_slugs(slugs: list[str], nodes: set[str]) -> int:
    missing = [s for s in slugs if s not in nodes]
    if missing:
        print(
            f"error: slug(s) not found in wiki: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    nodes, outbound, inbound = build_graph(args.wiki_root)
    src = _resolve_slug(args.source, nodes)
    dst = _resolve_slug(args.target, nodes)
    rc = _check_slugs([src, dst], nodes)
    if rc:
        return rc
    adj = _undirected_adjacency(nodes, outbound, inbound)
    t0 = time.monotonic()
    path = shortest_path(adj, src, dst)
    duration_ms = int((time.monotonic() - t0) * 1000)
    result = {
        "op": "path",
        "source": src,
        "target": dst,
        "path": path,
        "hops": (len(path) - 1) if path else None,
        "connected": path is not None,
        "duration_ms": duration_ms,
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if not path:
        print(f"No path found from [[{src}]] to [[{dst}]] (disconnected components).")
        return 0
    print(f"Path from [[{src}]] to [[{dst}]] ({result['hops']} hops):")
    for i, slug in enumerate(path):
        prefix = "  " if i == 0 else "    → "
        print(f"{prefix}[[{slug}]]")
    return 0


def cmd_neighbors(args: argparse.Namespace) -> int:
    nodes, outbound, inbound = build_graph(args.wiki_root)
    slug = _resolve_slug(args.slug, nodes)
    rc = _check_slugs([slug], nodes)
    if rc:
        return rc
    adj = _undirected_adjacency(nodes, outbound, inbound)
    t0 = time.monotonic()
    pairs = neighbors_within(adj, slug, args.depth)
    duration_ms = int((time.monotonic() - t0) * 1000)
    by_depth: dict[int, list[str]] = defaultdict(list)
    for s, d in pairs:
        by_depth[d].append(s)
    result = {
        "op": "neighbors",
        "slug": slug,
        "depth": args.depth,
        "total": len(pairs),
        "by_depth": {str(d): by_depth[d] for d in sorted(by_depth)},
        "duration_ms": duration_ms,
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"Neighbors of [[{slug}]] (depth {args.depth}): {len(pairs)} pages")
    if not pairs:
        print("(no neighbors within depth)")
        return 0
    print()
    for d in sorted(by_depth):
        slugs = by_depth[d]
        print(f"Depth {d} ({len(slugs)} pages):")
        for s in slugs:
            print(f"  - [[{s}]]")
        print()
    return 0


def cmd_subgraph(args: argparse.Namespace) -> int:
    nodes, outbound, inbound = build_graph(args.wiki_root)
    slug = _resolve_slug(args.slug, nodes)
    rc = _check_slugs([slug], nodes)
    if rc:
        return rc
    adj = _undirected_adjacency(nodes, outbound, inbound)
    t0 = time.monotonic()
    pages, edges = subgraph_around(adj, slug)
    duration_ms = int((time.monotonic() - t0) * 1000)
    result = {
        "op": "subgraph",
        "slug": slug,
        "page_count": len(pages),
        "edge_count": len(edges),
        "pages": pages,
        "edges": [list(e) for e in edges],
        "duration_ms": duration_ms,
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(
        f"Subgraph reachable from [[{slug}]]: "
        f"{len(pages)} pages, {len(edges)} edges"
    )
    print()
    print("Pages:")
    for p in pages:
        print(f"  - [[{p}]]")
    print()
    if edges:
        print(f"Edges ({len(edges)} undirected):")
        # Show a sample if very large
        sample = edges if len(edges) <= 40 else edges[:40]
        for a, b in sample:
            print(f"  [[{a}]] — [[{b}]]")
        if len(edges) > 40:
            print(f"  … ({len(edges) - 40} more)")
    return 0


def cmd_retrieve(args: argparse.Namespace) -> int:
    """Topic -> page set for /wiki-summarize (and /wiki-ask routing).

    If the topic resolves to an existing slug: `SHAPE_A <slug> <n_pages>
    <inbound_count>`, then one wiki-relative path per page (anchor first, then
    its neighbors within --depth hops). Otherwise: `SHAPE_B` (caller falls
    back to hybrid search). Topic comes from argv or, with --topic-stdin, from
    stdin verbatim (no shell escaping needed).
    """
    raw = sys.stdin.read() if args.topic_stdin else (args.topic or "")
    nodes, outbound, inbound = build_graph(args.wiki_root)
    slug = _resolve_slug(raw, nodes)
    if slug not in nodes:  # "Agent Memory Systems" -> agent-memory-systems
        import re
        kebab = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
        slug = kebab if kebab in nodes else slug
    if slug not in nodes:
        print("SHAPE_B")
        return 0
    from constants import PAGE_TYPES
    slug_to_path: dict[str, str] = {}
    for page_type in PAGE_TYPES:
        type_dir = args.wiki_root / "pages" / page_type
        if type_dir.is_dir():
            for md in type_dir.glob("*.md"):
                slug_to_path[md.stem] = f"pages/{page_type}/{md.name}"
    adj = _undirected_adjacency(nodes, outbound, inbound)
    pages = [slug] + [s for s, _ in neighbors_within(adj, slug, args.depth)]
    print("SHAPE_A", slug, len(pages), len(inbound.get(slug, set())))
    for s in pages:
        print(slug_to_path.get(s, f"# UNRESOLVED:{s}"))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run path / neighbors / subgraph queries over the wikilink graph"
    )
    p.add_argument("wiki_root", type=Path, help="Path to the wiki root")
    sub = p.add_subparsers(dest="op", required=True, metavar="OP")

    json_flag_kwargs = dict(action="store_true", help="Emit JSON instead of human format")

    sp_path = sub.add_parser("path", help="Shortest path between two slugs")
    sp_path.add_argument("source")
    sp_path.add_argument("target")
    sp_path.add_argument("--json", **json_flag_kwargs)
    sp_path.set_defaults(func=cmd_path)

    sp_neigh = sub.add_parser("neighbors", help="Pages within N hops of a slug")
    sp_neigh.add_argument("slug")
    sp_neigh.add_argument(
        "--depth",
        type=int,
        default=1,
        help=(
            "Hop limit (default 1). Default chosen for dense wikis where depth=2 "
            "from a hub commonly reaches most of the graph (e.g., agent-harness at "
            "106 inbound: depth-1 → 120 pages, depth-2 → 350/363). Pass --depth 2 or 3 "
            "for wider neighborhoods."
        ),
    )
    sp_neigh.add_argument("--json", **json_flag_kwargs)
    sp_neigh.set_defaults(func=cmd_neighbors)

    sp_sub = sub.add_parser("subgraph", help="Connected component containing a slug")
    sp_sub.add_argument("slug")
    sp_sub.add_argument("--json", **json_flag_kwargs)
    sp_sub.set_defaults(func=cmd_subgraph)

    sp_ret = sub.add_parser("retrieve", help="Topic -> SHAPE_A page paths (slug match) or SHAPE_B")
    sp_ret.add_argument("topic", nargs="?", default=None)
    sp_ret.add_argument("--topic-stdin", action="store_true", help="Read the topic verbatim from stdin")
    sp_ret.add_argument("--depth", type=int, default=1, help="Hop limit (default 1)")
    sp_ret.set_defaults(func=cmd_retrieve)

    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    wiki_root: Path = args.wiki_root.resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(
            f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)",
            file=sys.stderr,
        )
        return 2
    args.wiki_root = wiki_root
    if hasattr(args, "depth") and args.depth < 1:
        print("error: --depth must be >= 1", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
