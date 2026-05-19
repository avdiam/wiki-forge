#!/usr/bin/env python3
"""Append one row to wiki/.meta/query_log.jsonl.

Called by `/wiki-query` (both paths), `/wiki-graph` (graph queries), and
`/wiki-summarize` (topic-coverage reports). The `query_skill` field
distinguishes the row's origin:

  - `query_skill: "query"` — `/wiki-query` invocation. Path A (inline) leaves
    most cost fields null; Path B parses them from the librarian's <usage>.
    `search_mode` is required for query rows.
  - `query_skill: "graph"` — `/wiki-graph` invocation. Pure stdlib script,
    no model call: all cost fields and `search_mode` are null. `pages_consulted`
    holds the result set (path / neighbors / subgraph pages); `duration_ms`
    is the script wall-clock.
  - `query_skill: "summarize"` — `/wiki-summarize` invocation. Always
    dispatches the librarian (survey-mode), so cost fields are parsed from
    `<usage>` like query Path B. `search_mode` is `"slug-anchored"` for
    Shape A (topic resolves to an existing slug → graph_query.neighbors_within)
    or `"hybrid"` for Shape B (fuzzy fallback → hybrid_search.search).

Row schema:
    {
      "timestamp": "2026-04-20T14:31:12",
      "query_skill": "query",
      "query": "what is RAG",
      "query_variations": ["retrieval-augmented generation", "RAG pipeline"],
      "search_mode": "hybrid",
      "pages_consulted": ["rag", "llm-wiki-pattern"],
      "top_score": 0.82,
      "filed_as": null,
      "total_tokens": null, "input_tokens": null,
      "cache_creation_input_tokens": null, "cache_read_input_tokens": null,
      "output_tokens": null, "tool_uses": null,
      "duration_ms": 8100
    }

`search_mode` values: `"inline"` (Path A), `"hybrid" | "hybrid_zvec" |
"bm25_only"` (Path B retrieval modes from `hybrid_search.py`),
`"slug-anchored"` (`/wiki-summarize` Shape A), or `null` (graph rows).

On Path A (`search_mode: inline`) all six cost fields are `null` — no
subagent dispatched, no `<usage>` reaches the caller; `duration_ms`
(main-session wall-clock) is the Path-A cost proxy. On Path B every
cost field is parsed independently from the librarian's `<usage>`;
fields the block omits stay `null`.

For `query_skill: "graph"`, `search_mode` and all six cost fields are
`null`; `query` carries the raw subcommand + args
(e.g., `"neighbors lethal-trifecta --depth 2"`).

For `query_skill: "summarize"`, `search_mode` is `"slug-anchored"`
(Shape A) or `"hybrid"` (Shape B). `filed_as` records the coverage
synthesis slug if filed, else `null`. Empty Shape B results write no
row (skill emits the dual-onramp inline without librarian dispatch).

`query_variations` is the JSON array of paraphrase variations
`/wiki-query` passed to `hybrid_search.py`'s `--queries-json` flag,
*excluding* the canonical first query (which lives in `query`).
`null` on Path A and on graph rows; `null` or `[]` on N=1 Path B.

Usage:
    # /wiki-query Path A
    python append_query_log.py --wiki <wiki-root> \
        --query "what is RAG" --search-mode inline \
        --pages-consulted "rag,llm-wiki-pattern" \
        --duration-ms 8100

    # /wiki-graph
    python append_query_log.py --wiki <wiki-root> --query-skill graph \
        --query "neighbors lethal-trifecta --depth 2" \
        --pages-consulted "agent-harness,prompt-injection,..." \
        --duration-ms 800

    # /wiki-summarize Shape A (slug-anchored)
    python append_query_log.py --wiki <wiki-root> --query-skill summarize \
        --query "rag" --search-mode slug-anchored \
        --pages-consulted "rag,agentic-rag,..." \
        --filed-as "rag-coverage" \
        --total-tokens 18000 --tool-uses 14 --duration-ms 32000
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


VALID_SEARCH_MODES = {"inline", "hybrid", "hybrid_zvec", "bm25_only", "slug-anchored"}
VALID_QUERY_SKILLS = {"query", "graph", "summarize"}


def optional_int(raw: str) -> int | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return int(raw)


def optional_float(raw: str) -> float | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return float(raw)


def optional_str(raw: str) -> str | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return raw


def optional_str_list(raw: str) -> "list[str] | None":
    """Parse --query-variations: empty/null → None, JSON array → list[str].

    Anything that isn't a JSON array of strings raises argparse error.
    Phase 2 telemetry: paraphrase variations passed to hybrid_search.py
    beyond the canonical first query. Null on Path A and N=1 Path B.
    """
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"--query-variations must be JSON or null: {e}")
    if not isinstance(parsed, list) or not all(isinstance(s, str) for s in parsed):
        raise argparse.ArgumentTypeError("--query-variations must be a JSON array of strings")
    return parsed if parsed else None


def split_slugs(raw: str) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    p.add_argument("--query", required=True, help="The user's raw query string")
    p.add_argument(
        "--query-skill",
        default="query",
        choices=sorted(VALID_QUERY_SKILLS),
        help=(
            "Origin skill: 'query' (/wiki-query), 'graph' (/wiki-graph), "
            "or 'summarize' (/wiki-summarize). Default: query."
        ),
    )
    p.add_argument(
        "--search-mode",
        default=None,
        help=(
            f"One of: {', '.join(sorted(VALID_SEARCH_MODES))}. "
            "Required for query_skill in {query, summarize}; null for query_skill=graph."
        ),
    )
    p.add_argument("--pages-consulted", default="", help="Comma-separated slugs (or paths)")
    p.add_argument("--top-score", type=optional_float, default=None, help="Normalized [0,1]; null on inline")
    p.add_argument("--filed-as", type=optional_str, default=None, help="Synthesis slug if filed, else null")
    p.add_argument("--total-tokens", type=optional_int, default=None, help="From <usage>; null on Path A")
    p.add_argument("--input-tokens", type=optional_int, default=None,
                   help="From <usage>; null on Path A or when not surfaced")
    p.add_argument("--cache-creation-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-write field")
    p.add_argument("--cache-read-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-hit field")
    p.add_argument("--output-tokens", type=optional_int, default=None,
                   help="From <usage>; null on Path A or when not surfaced")
    p.add_argument("--tool-uses", type=optional_int, default=None, help="From <usage>; null on Path A")
    p.add_argument("--duration-ms", type=int, required=True, help="Wall-clock ms for the query turn")
    p.add_argument(
        "--query-variations",
        type=optional_str_list,
        default=None,
        help="Phase 2: JSON array of paraphrase variations beyond the canonical query, or null",
    )
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "query_skill": args.query_skill,
        "query": args.query,
        "query_variations": args.query_variations,
        "search_mode": args.search_mode,
        "pages_consulted": split_slugs(args.pages_consulted),
        "top_score": args.top_score,
        "filed_as": args.filed_as,
        "total_tokens": args.total_tokens,
        "input_tokens": args.input_tokens,
        "cache_creation_input_tokens": args.cache_creation_input_tokens,
        "cache_read_input_tokens": args.cache_read_input_tokens,
        "output_tokens": args.output_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
    }


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.query_skill in {"query", "summarize"}:
        if not args.search_mode:
            print(
                f"error: --search-mode is required when --query-skill={args.query_skill}",
                file=sys.stderr,
            )
            return 2
        if args.search_mode not in VALID_SEARCH_MODES:
            print(
                f"error: invalid search_mode '{args.search_mode}'. "
                f"Valid: {', '.join(sorted(VALID_SEARCH_MODES))}",
                file=sys.stderr,
            )
            return 2
    elif args.query_skill == "graph":
        # search_mode is null for graph rows; ignore any value passed
        args.search_mode = None

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    meta_dir = wiki_root / ".meta"
    meta_dir.mkdir(exist_ok=True)
    log_path = meta_dir / "query_log.jsonl"

    row = build_row(args)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
