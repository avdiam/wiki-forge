#!/usr/bin/env python3
"""Analyze wiki telemetry logs — ingest_log.jsonl + query_log.jsonl.

Reports:
  - Page-hit histogram (which pages queries consulted, how often)
  - Query repetition (exact-string + canonicalized)
  - Dead pages (wiki pages never consulted by any logged query)
  - Bilateral amortization ratio (query cost vs ingest cost, per page and
    in aggregate)

Cost attribution:
  - Each ingest's total_tokens / duration_ms is divided equally among
    pages_created + pages_updated (pages "earn" their ingest cost).
  - Each query's total_tokens / duration_ms is divided equally among
    pages_consulted (pages "earn" query-retrieval credit).
  - Σ per-page shares across pages = total system cost (conservation).

Path-A caveat: inline queries have total_tokens=null (no subagent
dispatched, no <usage> block). Token-measured ratio excludes those rows;
duration-measured ratio covers all paths.

Usage:
    python query_stats.py <wiki-root> [--json] [--top N]
    python query_stats.py <wiki-root> --ingest-log <path> --query-log <path>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from constants import PAGE_TYPES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"warning: {path}:{i}: skipping bad JSONL — {e}", file=sys.stderr)
    return rows


def load_wiki_pages(wiki_root: Path) -> set[str]:
    """Collect page identifiers in 'type/slug' form — matches logged paths."""
    pages: set[str] = set()
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if type_dir.is_dir():
            for md in type_dir.glob("*.md"):
                pages.add(f"{page_type}/{md.stem}")
    return pages


def canonicalize(query: str) -> str:
    """Lowercase + strip punctuation + sorted unique token set."""
    cleaned = _PUNCT_RE.sub(" ", query.lower())
    tokens = sorted(set(t for t in cleaned.split() if t))
    return " ".join(tokens)


def _normalize_page_id(raw: str) -> str:
    """Strip .md suffix and normalize path separators."""
    s = raw.strip().replace("\\", "/")
    if s.endswith(".md"):
        s = s[:-3]
    return s


def compute_page_hits(queries: list[dict]) -> Counter:
    hits: Counter = Counter()
    for q in queries:
        for page in q.get("pages_consulted") or []:
            hits[_normalize_page_id(page)] += 1
    return hits


def compute_query_repetition(queries: list[dict]) -> tuple[Counter, Counter]:
    exact = Counter(q.get("query", "") for q in queries if q.get("query"))
    canonical = Counter(canonicalize(q.get("query", "")) for q in queries if q.get("query"))
    return exact, canonical


def compute_dead_pages(all_pages: set[str], hits: Counter) -> list[str]:
    """Pages in the wiki that no query has ever consulted.

    Matches both 'type/slug' and bare 'slug' forms in the log — wikilinks
    in the librarian's output are slug-only, but full paths also appear.
    """
    hit_full: set[str] = set()
    hit_bare: set[str] = set()
    for logged in hits:
        hit_full.add(logged)
        if "/" in logged:
            hit_bare.add(logged.split("/")[-1])
        else:
            hit_bare.add(logged)

    dead: list[str] = []
    for p in all_pages:
        if p in hit_full:
            continue
        bare = p.split("/")[-1]
        if bare in hit_bare:
            continue
        dead.append(p)
    return sorted(dead)


def compute_amortization(ingests: list[dict], queries: list[dict]) -> dict:
    per_page_ingest_tokens: dict[str, float] = defaultdict(float)
    per_page_ingest_duration: dict[str, float] = defaultdict(float)
    for ing in ingests:
        pages = [_normalize_page_id(p) for p in (ing.get("pages_created") or []) + (ing.get("pages_updated") or [])]
        if not pages:
            continue
        tokens = ing.get("total_tokens")
        duration = ing.get("duration_ms")
        if tokens is not None:
            share = tokens / len(pages)
            for p in pages:
                per_page_ingest_tokens[p] += share
        if duration is not None:
            share = duration / len(pages)
            for p in pages:
                per_page_ingest_duration[p] += share

    per_page_query_tokens: dict[str, float] = defaultdict(float)
    per_page_query_duration: dict[str, float] = defaultdict(float)
    for q in queries:
        pages = [_normalize_page_id(p) for p in q.get("pages_consulted") or []]
        if not pages:
            continue
        tokens = q.get("total_tokens")
        duration = q.get("duration_ms")
        if tokens is not None:
            share = tokens / len(pages)
            for p in pages:
                per_page_query_tokens[p] += share
        if duration is not None:
            share = duration / len(pages)
            for p in pages:
                per_page_query_duration[p] += share

    total_ingest_tokens = sum(per_page_ingest_tokens.values())
    total_query_tokens = sum(per_page_query_tokens.values())
    total_ingest_duration = sum(per_page_ingest_duration.values())
    total_query_duration = sum(per_page_query_duration.values())

    per_page_ratio: dict[str, float] = {}
    for page, ingest_cost in per_page_ingest_tokens.items():
        if ingest_cost > 0:
            per_page_ratio[page] = per_page_query_tokens.get(page, 0.0) / ingest_cost

    return {
        "per_page_ingest_tokens": dict(per_page_ingest_tokens),
        "per_page_query_tokens": dict(per_page_query_tokens),
        "per_page_ingest_duration": dict(per_page_ingest_duration),
        "per_page_query_duration": dict(per_page_query_duration),
        "per_page_token_ratio": per_page_ratio,
        "total_ingest_tokens": total_ingest_tokens,
        "total_query_tokens": total_query_tokens,
        "total_ingest_duration_ms": total_ingest_duration,
        "total_query_duration_ms": total_query_duration,
        "aggregate_token_ratio": (total_query_tokens / total_ingest_tokens) if total_ingest_tokens else None,
        "aggregate_duration_ratio": (total_query_duration / total_ingest_duration) if total_ingest_duration else None,
    }


def build_report_dict(ingests: list[dict], queries: list[dict], all_pages: set[str], top: int) -> dict:
    hits = compute_page_hits(queries)
    exact, canonical = compute_query_repetition(queries)
    dead = compute_dead_pages(all_pages, hits)
    am = compute_amortization(ingests, queries)
    path_a_count = sum(1 for q in queries if q.get("total_tokens") is None)

    return {
        "counts": {
            "ingests": len(ingests),
            "queries": len(queries),
            "wiki_pages": len(all_pages),
            "path_a_queries": path_a_count,
        },
        "page_hits_top": hits.most_common(top),
        "query_repetition": {
            "exact_duplicates": [{"query": q, "count": c} for q, c in exact.most_common(top) if c > 1],
            "canonical_duplicates": [{"canonical": q, "count": c} for q, c in canonical.most_common(top) if c > 1],
            "redundant_exact": sum(c - 1 for _, c in exact.items() if c > 1),
            "redundant_canonical": sum(c - 1 for _, c in canonical.items() if c > 1),
        },
        "dead_pages": dead,
        "amortization": am,
    }


def render_human(report: dict, top: int) -> str:
    lines: list[str] = []
    c = report["counts"]
    lines.append("=" * 60)
    lines.append("Wiki Telemetry Report")
    lines.append("=" * 60)
    lines.append(f"Ingests logged:  {c['ingests']}")
    lines.append(f"Queries logged:  {c['queries']}")
    lines.append(f"Wiki pages:      {c['wiki_pages']}")
    if c["path_a_queries"]:
        lines.append("")
        lines.append(
            f"* {c['path_a_queries']} queries have total_tokens=null (Path A — no <usage>).\n"
            f"  Token-measured ratio excludes these; duration-measured covers all."
        )

    lines.append("")
    lines.append(f"-- Page hits (top {top}) --")
    if not report["page_hits_top"]:
        lines.append("  (no queries logged)")
    for page, count in report["page_hits_top"]:
        lines.append(f"  {count:4d}  {page}")

    lines.append("")
    lines.append("-- Query repetition --")
    rep = report["query_repetition"]
    lines.append(f"  Redundant exact-string invocations:  {rep['redundant_exact']}")
    for item in rep["exact_duplicates"]:
        lines.append(f"    x{item['count']}  {item['query'][:72]}")
    lines.append(f"  Redundant canonicalized invocations: {rep['redundant_canonical']}")
    for item in rep["canonical_duplicates"]:
        lines.append(f"    x{item['count']}  {item['canonical'][:72]}")

    lines.append("")
    lines.append("-- Dead pages (never consulted by any query) --")
    dead = report["dead_pages"]
    lines.append(f"  Count: {len(dead)} / {c['wiki_pages']}")
    for p in dead[:top]:
        lines.append(f"  - {p}")
    if len(dead) > top:
        lines.append(f"  ... +{len(dead) - top} more")

    lines.append("")
    lines.append("-- Bilateral amortization --")
    am = report["amortization"]
    lines.append(f"  Ingest cost (tokens, measured):  {am['total_ingest_tokens']:>14,.0f}")
    lines.append(f"  Query cost  (tokens, measured):  {am['total_query_tokens']:>14,.0f}")
    tr = am["aggregate_token_ratio"]
    lines.append(f"  Token-measured ratio:            {tr:>14.3f}" if tr is not None else "  Token-measured ratio:            n/a")
    lines.append(f"  Ingest cost (duration ms):       {am['total_ingest_duration_ms']:>14,.0f}")
    lines.append(f"  Query cost  (duration ms):       {am['total_query_duration_ms']:>14,.0f}")
    dr = am["aggregate_duration_ratio"]
    lines.append(f"  Duration-measured ratio:         {dr:>14.3f}" if dr is not None else "  Duration-measured ratio:         n/a")
    lines.append("  (ratio > 1.0 = retrieval has paid back ingest cost; < 1.0 = under-retrieved)")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Analyze wiki telemetry logs")
    p.add_argument("wiki_root", help="Path to the wiki root")
    p.add_argument("--ingest-log", default=None, help="Override path (default: <wiki>/.meta/ingest_log.jsonl)")
    p.add_argument("--query-log", default=None, help="Override path (default: <wiki>/.meta/query_log.jsonl)")
    p.add_argument("--top", type=int, default=10, help="Rows to show in each section (default: 10)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human report")
    args = p.parse_args(argv)

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    ingest_path = Path(args.ingest_log).resolve() if args.ingest_log else wiki_root / ".meta" / "ingest_log.jsonl"
    query_path = Path(args.query_log).resolve() if args.query_log else wiki_root / ".meta" / "query_log.jsonl"

    ingests = load_jsonl(ingest_path)
    queries = load_jsonl(query_path)
    all_pages = load_wiki_pages(wiki_root)

    report = build_report_dict(ingests, queries, all_pages, args.top)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_human(report, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
