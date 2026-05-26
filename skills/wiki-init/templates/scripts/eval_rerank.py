#!/usr/bin/env python3
"""Eval harness for Phase 4 typed-edge graph re-rank.

Replays historical queries from `wiki/.meta/query_log.jsonl` through both
the baseline `hybrid_search` pipeline and the `--rerank-typed-edges`
pipeline, then reports per-query rank-shift diffs and an aggregate summary.

Designed to answer one question before flipping the rerank-on-by-default
ship gate: does signal #1 (direct typed edges to top-N seeds) move
results in a way that matches the queries the wiki actually gets, or is
the typed-edge surface too sparse to justify flag-on?

Usage:
    python eval_rerank.py <wiki-root>
    python eval_rerank.py <wiki-root> --top-k 10
    python eval_rerank.py <wiki-root> --rerank-lambda 0.05
    python eval_rerank.py <wiki-root> --json    # machine-readable output

Reads from `<wiki-root>/.meta/query_log.jsonl`. Only rows with a non-empty
`query` field and `search_mode` in (`hybrid`, `hybrid_zvec`, `bm25_only`,
`""` — meaning legacy pre-skill-field rows) are replayed; graph- and
summarize-only rows are skipped.

Pure-stdlib aside from the hybrid_search import. Reuses the existing
`search()` function rather than reimplementing the pipeline so the eval
matches production exactly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make hybrid_search importable when this script lives in the same dir
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hybrid_search import search  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


HYBRID_MODES = {"hybrid", "hybrid_zvec", "bm25_only", ""}


def load_query_log(wiki_root: Path) -> list[dict]:
    """Read query_log.jsonl and return rows worth replaying.

    Filters to rows where the query was run through hybrid_search (not
    pure-graph or pure-summarize), and where the query string is non-empty.
    Legacy rows pre-`query_skill` field are kept (their `search_mode` is
    "hybrid" or similar from before the skill split).
    """
    path = wiki_root / ".meta" / "query_log.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("query"):
            continue
        mode = row.get("search_mode") or ""
        skill = row.get("query_skill") or ""
        # Skip pure-graph and pure-summarize rows (graph_query / summarize
        # paths don't go through hybrid_search). Summarize rows that ran
        # hybrid (mode=hybrid) ARE replayed — they exercised the same code.
        if skill in ("graph",):
            continue
        if mode not in HYBRID_MODES:
            continue
        rows.append(row)
    return rows


def rank_diff(
    baseline: list[dict],
    rerank: list[dict],
) -> list[dict]:
    """Compare two result lists by slug. Returns per-slug rank-shift records.

    Each record: {slug, baseline_rank, rerank_rank, shift, base_score, new_score}.
    `baseline_rank` and `rerank_rank` are 1-indexed; None if not in that list.
    `shift` is `baseline_rank - rerank_rank` (positive = moved up).
    Slugs appearing only in one list are kept (with None on the other side).
    """
    base_index = {r["slug"]: (i + 1, r["score"]) for i, r in enumerate(baseline)}
    rerank_index = {r["slug"]: (i + 1, r["score"]) for i, r in enumerate(rerank)}
    all_slugs = set(base_index) | set(rerank_index)
    records = []
    for slug in all_slugs:
        b_rank, b_score = base_index.get(slug, (None, None))
        r_rank, r_score = rerank_index.get(slug, (None, None))
        shift = (
            (b_rank - r_rank) if (b_rank is not None and r_rank is not None) else None
        )
        records.append({
            "slug": slug,
            "baseline_rank": b_rank,
            "rerank_rank": r_rank,
            "shift": shift,
            "baseline_score": b_score,
            "rerank_score": r_score,
        })
    # Sort by rerank_rank (then baseline_rank) so highest-relevance is first
    def sort_key(rec):
        return (
            rec["rerank_rank"] if rec["rerank_rank"] is not None else 9999,
            rec["baseline_rank"] if rec["baseline_rank"] is not None else 9999,
        )
    records.sort(key=sort_key)
    return records


def evaluate(
    wiki_root: Path,
    rows: list[dict],
    top_k: int,
    rerank_top_n_seeds: int,
    rerank_lambda: float,
    rerank_window: int,
) -> dict:
    """Replay each row through baseline + rerank pipelines, return report."""
    per_query = []
    boosted_total = 0
    shuffled_total = 0
    for i, row in enumerate(rows, 1):
        q = row["query"]
        variations = row.get("query_variations")
        # query_variations is stored as JSON string OR null/"". Normalize.
        if isinstance(variations, str) and variations.strip():
            try:
                variations = json.loads(variations)
            except json.JSONDecodeError:
                variations = None
        if not isinstance(variations, list):
            variations = []
        queries = [q] + [v for v in variations if isinstance(v, str)]

        baseline_result = search(wiki_root, queries, top_k=top_k)
        rerank_result = search(
            wiki_root, queries, top_k=top_k,
            rerank_typed_edges=True,
            rerank_top_n_seeds=rerank_top_n_seeds,
            rerank_lambda=rerank_lambda,
            rerank_window=rerank_window,
        )

        diffs = rank_diff(baseline_result["results"], rerank_result["results"])
        # `shuffled` = at least one slug with a non-zero shift inside top_k
        shuffled = any(
            d["shift"] is not None and d["shift"] != 0 for d in diffs
        )
        # `boosted` = rerank actually applied (search_mode ends with _graph_rerank)
        boosted = rerank_result["search_mode"].endswith("_graph_rerank")
        if boosted:
            boosted_total += 1
        if shuffled:
            shuffled_total += 1

        per_query.append({
            "row_number": i,
            "query": q,
            "n_variations": len(queries) - 1,
            "baseline_mode": baseline_result["search_mode"],
            "rerank_mode": rerank_result["search_mode"],
            "boosted": boosted,
            "shuffled": shuffled,
            "diffs": diffs,
        })

    return {
        "total_queries": len(rows),
        "boosted_count": boosted_total,
        "shuffled_count": shuffled_total,
        "config": {
            "top_k": top_k,
            "rerank_top_n_seeds": rerank_top_n_seeds,
            "rerank_lambda": rerank_lambda,
            "rerank_window": rerank_window,
        },
        "per_query": per_query,
    }


def render_human(report: dict) -> str:
    """Format report for human reading."""
    cfg = report["config"]
    lines = [
        "Phase 4 typed-edge rerank — eval over historical queries",
        "=" * 60,
        f"Config: top_k={cfg['top_k']} seeds={cfg['rerank_top_n_seeds']} "
        f"lambda={cfg['rerank_lambda']} window={cfg['rerank_window']}",
        f"Replayed: {report['total_queries']} queries",
        f"  Rerank fired (≥1 typed edge to seeds): {report['boosted_count']}",
        f"  Top-{cfg['top_k']} reordered:                 {report['shuffled_count']}",
        "",
    ]
    for entry in report["per_query"]:
        lines.append(
            f"#{entry['row_number']} [{entry['baseline_mode']} → "
            f"{entry['rerank_mode']}]  shuffled={entry['shuffled']}"
        )
        lines.append(f"  Q: {entry['query'][:90]}")
        moved = [d for d in entry["diffs"] if d["shift"] not in (None, 0)]
        if moved:
            for d in moved:
                lines.append(
                    f"    {d['slug']:<55}  "
                    f"rank {d['baseline_rank']}→{d['rerank_rank']}  "
                    f"({d['baseline_score']:.3f}→{d['rerank_score']:.3f})"
                )
        elif entry["boosted"]:
            lines.append("    (boosted but no top-k rank change)")
        else:
            lines.append("    (no rerank signal)")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Eval Phase 4 typed-edge graph rerank on historical queries"
    )
    p.add_argument("wiki_root", type=Path)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--rerank-top-n-seeds", type=int, default=3)
    p.add_argument("--rerank-lambda", type=float, default=0.05)
    p.add_argument("--rerank-window", type=int, default=30)
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human format")
    args = p.parse_args()

    wiki_root = args.wiki_root.resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} is not a wiki root", file=sys.stderr)
        return 2

    rows = load_query_log(wiki_root)
    if not rows:
        print("error: no replayable rows in query_log.jsonl", file=sys.stderr)
        return 2

    report = evaluate(
        wiki_root, rows,
        top_k=args.top_k,
        rerank_top_n_seeds=args.rerank_top_n_seeds,
        rerank_lambda=args.rerank_lambda,
        rerank_window=args.rerank_window,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
