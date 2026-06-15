#!/usr/bin/env python3
"""Append one row to wiki/.meta/lint_log.jsonl.

Called from two paths:

1. Post-commit mechanical lint in the wiki-ingestor subagent (every ingest).
   Pass `--lint-type mechanical`. Cost fields (total_tokens, tool_uses) are
   null — stdlib script, no subagent.

2. Full three-pass lint in the wiki-linter subagent (every /wiki-lint).
   Pass `--lint-type full`. Cost fields parsed from the <usage> block surfaced
   by the Agent tool on subagent completion; pass empty string or "null" when
   unavailable.

`--counts` is a JSON object mapping each lint_wiki.py check name to the number
of pages it flagged. Pass `{}` when the lint came up completely clean.

Row schema:
    {
      "timestamp": "2026-04-21T14:32:04",
      "lint_type": "full",
      "ingest_count_at_run": 27,
      "total_pages": 93,
      "counts": {
        "broken_links": 0, "orphans": 2, "missing_frontmatter": 0,
        "stale": 0, "low_confidence": 1, "index_drift": 0,
        "source_backlog": 0, "stale_source": 0,
        "overview_staleness": 0, "single_page_categories": 1,
        "long_summary": 0, "needs_verification": 3,
        "excessive_ambiguous": 0,
        "unacknowledged_contradictions": 0, "category_issues": 0
      },
      "verdict": "issues",
      "semantic_pass_ran": true,
      "total_tokens": 48210, "input_tokens": 95,
      "cache_creation_input_tokens": 6120, "cache_read_input_tokens": 41610,
      "output_tokens": 385, "tool_uses": 18,
      "duration_ms": 184500,
      "commit_hash": "a1b2c3d"
    }

`lint_type: mechanical` rows have `semantic_pass_ran: false`, all six
cost fields null (stdlib script, no subagent), and typically
`commit_hash: null` (mechanical runs don't commit on their own).
`lint_type: full` rows have `semantic_pass_ran: true` and cost fields
populated per-field from the linter subagent's `<usage>`; missing
sub-fields stay null. `commit_hash` is populated only if the user
approved fixes that produced a commit. `ingest_count_at_run` is a
monotonic snapshot — the count of ingest entries in `log.md` at the
moment lint fired — and is the anchor for "ingests since last full
lint" derivations (current ingest count minus max `ingest_count_at_run`
over rows with `lint_type: full`).

Usage:
    python append_lint_log.py --wiki <wiki-root> \
        --lint-type mechanical \
        --ingest-count-at-run 27 --total-pages 93 \
        --counts '{"broken_links": 0, "orphans": 2}' \
        --verdict issues \
        --semantic-pass-ran false \
        --total-tokens "" --tool-uses "" --duration-ms 1200 \
        --commit-hash ""
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from _log_writer import append_jsonl, optional_int, optional_str, resolve_wiki_root, strict_bool


VALID_LINT_TYPES = {"mechanical", "full"}
VALID_VERDICTS = {"clean", "issues"}


def parse_counts(raw: str) -> dict:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"--counts must be valid JSON object: {e}")
    if not isinstance(obj, dict):
        raise argparse.ArgumentTypeError("--counts must be a JSON object")
    for k, v in obj.items():
        if not isinstance(v, int):
            raise argparse.ArgumentTypeError(f"--counts['{k}'] must be int, got {type(v).__name__}")
    return obj


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    p.add_argument("--lint-type", required=True, help=f"One of: {', '.join(sorted(VALID_LINT_TYPES))}")
    p.add_argument("--ingest-count-at-run", type=int, required=True,
                   help="Number of ingest entries in log.md at time of this lint")
    p.add_argument("--total-pages", type=int, required=True)
    p.add_argument("--counts", type=parse_counts, default={},
                   help="JSON object mapping check name -> flagged count; {} for clean")
    p.add_argument("--verdict", required=True, help=f"One of: {', '.join(sorted(VALID_VERDICTS))}")
    p.add_argument("--semantic-pass-ran", type=strict_bool, required=True,
                   help="true only when lint_type=full and Pass B actually ran")
    p.add_argument("--total-tokens", type=optional_int, default=None,
                   help="From <usage>; null for mechanical")
    p.add_argument("--input-tokens", type=optional_int, default=None,
                   help="From <usage>; null for mechanical or when not surfaced")
    p.add_argument("--cache-creation-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-write field")
    p.add_argument("--cache-read-input-tokens", type=optional_int, default=None,
                   help="From <usage>; the cache-hit field")
    p.add_argument("--output-tokens", type=optional_int, default=None,
                   help="From <usage>; null for mechanical or when not surfaced")
    p.add_argument("--tool-uses", type=optional_int, default=None,
                   help="From <usage>; null for mechanical")
    p.add_argument("--duration-ms", type=int, required=True, help="Wall-clock ms")
    p.add_argument("--commit-hash", type=optional_str, default=None,
                   help="Only populated when the lint produced a commit (fixes applied)")
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "lint_type": args.lint_type,
        "ingest_count_at_run": args.ingest_count_at_run,
        "total_pages": args.total_pages,
        "counts": args.counts,
        "verdict": args.verdict,
        "semantic_pass_ran": args.semantic_pass_ran,
        "total_tokens": args.total_tokens,
        "input_tokens": args.input_tokens,
        "cache_creation_input_tokens": args.cache_creation_input_tokens,
        "cache_read_input_tokens": args.cache_read_input_tokens,
        "output_tokens": args.output_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
        "commit_hash": args.commit_hash,
    }


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.lint_type not in VALID_LINT_TYPES:
        print(f"error: invalid lint_type '{args.lint_type}'. "
              f"Valid: {', '.join(sorted(VALID_LINT_TYPES))}", file=sys.stderr)
        return 2
    if args.verdict not in VALID_VERDICTS:
        print(f"error: invalid verdict '{args.verdict}'. "
              f"Valid: {', '.join(sorted(VALID_VERDICTS))}", file=sys.stderr)
        return 2
    if args.lint_type == "mechanical" and args.semantic_pass_ran:
        print("error: semantic_pass_ran=true is only valid for lint_type=full", file=sys.stderr)
        return 2

    wiki_root = resolve_wiki_root(args.wiki)
    if wiki_root is None:
        return 2

    log_path = wiki_root / ".meta" / "lint_log.jsonl"
    append_jsonl(log_path, build_row(args))

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
