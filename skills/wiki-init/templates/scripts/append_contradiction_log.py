#!/usr/bin/env python3
"""Append one row to wiki/.meta/contradiction_log.jsonl.

Called by `/wiki-resolve-contradictions` (one row per pair searched, not per
skill invocation) and back-filled twice — once by `/wiki-capture` on the
picked candidate URL, once by the wiki-ingestor at the takeaway gate's
new "Contradiction resolution" subsection.

Row schema (one row per `contradicts:` pair the skill searched; mutates
up to twice after creation — once at `/wiki-capture` time, once at
`/wiki-ingest` time):
    {
      "timestamp": "2026-05-22T11:45:03",
      "pair_key": "agents-md|2026-02-12-evaluating-agents-md-...",
      "pair_a_slug": "agents-md",
      "pair_a_type": "concept",
      "pair_b_slug": "2026-02-12-evaluating-agents-md-...",
      "pair_b_type": "source",
      "shape": "concept-source",
      "callout_excerpt": "This page's prior framing — context files as...",
      "candidates_returned": 5,
      "candidates_by_tier": {"tier_1": 2, "tier_2": 2, "tier_3": 1},
      "candidate_domains": [
        {"domain": "arxiv.org", "tier": "tier_1"},
        ...
      ],
      "include_tier_3_flag": false,
      "user_picked": "https://arxiv.org/abs/...",
      "user_action": "captured",
      "captured_to_vault": "vault/Web Articles/2026-05-22-...",
      "outcome": "a_wins",
      "resolved_at_ingest": "2026-05-22T12:10:55",
      "total_tokens": null, "tool_uses": null, "duration_ms": null
    }

`shape` is the `<type-a>-<type-b>` pair shape with types sorted
alphabetically (matches `list_contradictions.py`'s `shape` field —
e.g. `concept-source`, `source-source`, `comparison-concept`). Used for
post-hoc analysis of which pair shapes the skill is being run against.

`user_action` values:
  - `deferred`  — initial state; skill surfaced candidates, no action yet
  - `captured`  — user ran /wiki-capture on the picked URL (backfilled)
  - `skipped`   — user explicitly passed on this batch (reserved)

`outcome` values:
  - `pending`     — initial state; no resolution decision yet
  - `a_wins`      — side A's claim stands; aggregator page edited with
                    `> [!NOTE] Resolved:` callout citing the new source;
                    contradicts: edge removed from both sides
  - `b_wins`      — mirror of a_wins for side B
  - `both_stand`  — genuine disagreement preserved; contradicts: edge
                    downgraded to mutual `## Related` cross-link with
                    `(disagreement examined — both positions stand)`
                    annotation; both sides' contradicts: frontmatter
                    cleared
  - `defer`       — user opted to leave the contradiction in place for
                    now; no edits made

`resolved_at_ingest` is the dependent stamp: ISO timestamp on any of
`a_wins | b_wins | both_stand`; left `null` on `defer` or when no
ingest-gate decision has fired yet. Invariant:
`resolved_at_ingest != null` ⟺ `outcome ∈ {a_wins, b_wins, both_stand}`.

`candidate_domains` follows the same `{domain, tier}` shape as
`corroborate_log.jsonl` — domain is host + first path segment when
present (`github.com/anthropic`, `linkedin.com/in/<user>`) for tier-
classifier path-prefix patterns.

Cost fields (`total_tokens`, `tool_uses`, `duration_ms`, `websearch_calls`,
`context_tokens_post_pass`, `output_tokens_pass`) were `null` in v1 — the
harness emits no `<usage>` block for main-session (non-subagent) work. v2
(2026-06-07) recovers them after the fact from the Claude Code **session
transcript JSONL** via `session_cost_probe.py`, mirroring the
`append_corroborate_log.py` v2 instrumentation. The skill records the
transcript line count at the start of its per-pair loop, tags every row with a
`--batch-id`, then runs the probe and `--backfill-cost --batch-id <id>` to
stamp invocation-level cost onto all rows of the batch. Feeds the same
fork-vs-inline ROI question (dev/tech-debt.md "execution shape" entry) — median
`websearch_calls` / `context_tokens_post_pass`. `skill_version` records the
producing skill revision.

Usage:
    # Initial write — one row per pair searched (user_action defaults to deferred).
    python append_contradiction_log.py --wiki <wiki-root> \\
        --pair-key "agents-md|2026-02-12-evaluating-agents-md-..." \\
        --pair-a-slug agents-md --pair-a-type concept \\
        --pair-b-slug 2026-02-12-evaluating-agents-md-... --pair-b-type source \\
        --shape concept-source \\
        --callout-excerpt "This page's prior framing — context files as..." \\
        --candidates-returned 5 \\
        --candidates-by-tier '{"tier_1":2,"tier_2":2,"tier_3":1}' \\
        --candidate-urls '[{"url":"https://arxiv.org/...","tier":"tier_1"}, ...]' \\
        --include-tier-3-flag false

    # Backfill on capture — flips action, records the picked URL + vault path.
    python append_contradiction_log.py --wiki <wiki-root> --backfill \\
        --pair-key "agents-md|2026-02-12-..." \\
        --user-picked "https://arxiv.org/abs/..." \\
        --user-action captured \\
        --captured-to-vault "vault/Web Articles/2026-05-22-..."

    # Backfill at ingest — resolution decision on the most recent row
    # matching --pair-key. Outcome is one of a_wins | b_wins | both_stand
    # | defer. On a_wins/b_wins/both_stand stamps resolved_at_ingest with
    # the current timestamp; on defer leaves it null. Matches any
    # user_action so manual-edit paths (no /wiki-capture roundtrip) still
    # close the loop.
    python append_contradiction_log.py --wiki <wiki-root> --backfill-ingest \\
        --pair-key "agents-md|2026-02-12-..." --outcome a_wins

    # Backfill cost — stamp invocation-level cost (from session_cost_probe.py)
    # onto every row sharing --batch-id. Run once per invocation after the loop.
    python append_contradiction_log.py --wiki <wiki-root> --backfill-cost \\
        --batch-id 9803eda1-940 --total-tokens 41200 --tool-uses 7 \\
        --duration-ms 83100 --websearch-calls 6 \\
        --context-tokens-post 96800 --output-tokens-pass 5100
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from _log_writer import (
    append_jsonl,
    candidate_urls,
    candidates_dict,
    optional_bool,
    optional_int,
    optional_str,
    read_rows,
    resolve_wiki_root,
    write_rows,
)


VALID_USER_ACTIONS = {"captured", "skipped", "deferred"}
VALID_OUTCOMES = {"pending", "a_wins", "b_wins", "both_stand", "defer"}
RESOLVED_OUTCOMES = {"a_wins", "b_wins", "both_stand"}
VALID_PAGE_TYPES = {"concept", "entity", "comparison", "synthesis", "source"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--backfill",
        action="store_true",
        help="Mutate the most recent row matching --pair-key (and user_action=deferred unless --user-picked given) — sets user_picked / user_action / captured_to_vault.",
    )
    mode.add_argument(
        "--backfill-ingest",
        action="store_true",
        help="Stamp the resolution decision on the most recent row matching --pair-key. Requires --outcome a_wins|b_wins|both_stand|defer. On a_wins/b_wins/both_stand sets resolved_at_ingest=now; on defer leaves it null. Matches any user_action so manual-edit paths still close the loop.",
    )
    mode.add_argument(
        "--backfill-cost",
        action="store_true",
        help="Stamp invocation-level cost fields (from session_cost_probe.py) onto every row matching --batch-id. Run once per invocation after the per-pair loop.",
    )
    p.add_argument(
        "--outcome",
        choices=sorted(VALID_OUTCOMES - {"pending"}),
        default=None,
        help="Resolution outcome for --backfill-ingest. a_wins/b_wins/both_stand are resolving; defer leaves the contradiction in place.",
    )
    # Initial-write fields
    p.add_argument("--pair-key", default=None,
                   help="`<slug-a>|<slug-b>` with slugs sorted ascending. Required except for --backfill-cost (which matches on --batch-id).")
    p.add_argument("--pair-a-slug", default=None)
    p.add_argument("--pair-a-type", choices=sorted(VALID_PAGE_TYPES), default=None)
    p.add_argument("--pair-b-slug", default=None)
    p.add_argument("--pair-b-type", choices=sorted(VALID_PAGE_TYPES), default=None)
    p.add_argument("--shape", default=None, help="<type-a>-<type-b> with types sorted alphabetically")
    p.add_argument("--callout-excerpt", default=None)
    p.add_argument("--candidates-returned", type=optional_int, default=None)
    p.add_argument("--candidates-by-tier", type=candidates_dict, default={})
    p.add_argument(
        "--candidate-urls",
        type=candidate_urls,
        default=None,
        help="JSON array of {url, tier} records (one per filtered candidate). Hosts are extracted and stored as candidate_domains: [{domain, tier}, ...]. Pass '[]' for genuinely-zero-candidate runs; omit entirely on backfill operations.",
    )
    p.add_argument("--include-tier-3-flag", type=optional_bool, default=False)
    # Action fields
    p.add_argument("--user-picked", type=optional_str, default=None)
    p.add_argument("--user-action", choices=sorted(VALID_USER_ACTIONS), default="deferred")
    p.add_argument("--captured-to-vault", type=optional_str, default=None)
    # Invocation grouping (v2) — links the per-pair rows of one run.
    p.add_argument("--batch-id", type=optional_str, default=None,
                   help="Invocation id shared by every per-pair row of one run. Required on --backfill-cost.")
    p.add_argument("--skill-version", type=optional_str, default=None,
                   help="Skill revision that produced the row/batch.")
    # Cost fields — v2 backfilled from session_cost_probe.py (see module docstring).
    p.add_argument("--total-tokens", type=optional_int, default=None)
    p.add_argument("--tool-uses", type=optional_int, default=None)
    p.add_argument("--duration-ms", type=optional_int, default=None)
    p.add_argument("--websearch-calls", type=optional_int, default=None,
                   help="server_tool_use.web_search_requests summed over the invocation window.")
    p.add_argument("--context-tokens-post", type=optional_int, default=None,
                   help="Main-session context size at the last turn of the pass.")
    p.add_argument("--output-tokens-pass", type=optional_int, default=None,
                   help="output_tokens summed over the invocation window.")
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "pair_key": args.pair_key,
        "pair_a_slug": args.pair_a_slug,
        "pair_a_type": args.pair_a_type,
        "pair_b_slug": args.pair_b_slug,
        "pair_b_type": args.pair_b_type,
        "shape": args.shape,
        "callout_excerpt": args.callout_excerpt,
        "candidates_returned": args.candidates_returned,
        "candidates_by_tier": args.candidates_by_tier,
        "candidate_domains": args.candidate_urls,
        "include_tier_3_flag": args.include_tier_3_flag,
        "user_picked": args.user_picked,
        "user_action": args.user_action,
        "captured_to_vault": args.captured_to_vault,
        "outcome": "pending",
        "resolved_at_ingest": None,
        "batch_id": args.batch_id,
        "skill_version": args.skill_version,
        "total_tokens": args.total_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
        "websearch_calls": args.websearch_calls,
        "context_tokens_post_pass": args.context_tokens_post,
        "output_tokens_pass": args.output_tokens_pass,
    }


def _backfill_capture(rows: list[dict], args: argparse.Namespace) -> int:
    """Set user_picked / user_action / captured_to_vault on the most recent
    row matching pair_key AND user_action='deferred'."""
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("pair_key") == args.pair_key and r.get("user_action") == "deferred":
            if args.user_picked is not None:
                r["user_picked"] = args.user_picked
            r["user_action"] = args.user_action
            if args.captured_to_vault is not None:
                r["captured_to_vault"] = args.captured_to_vault
            return i
    raise SystemExit(f"error: no deferred row for pair_key={args.pair_key!r} to backfill")


def _backfill_ingest(rows: list[dict], args: argparse.Namespace) -> int:
    """Stamp the resolution outcome on the most recent row matching
    pair_key. outcome is always set; resolved_at_ingest is set only on
    a_wins/b_wins/both_stand. Matches any user_action so manual-edit
    paths (no /wiki-capture roundtrip) still close the loop."""
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("pair_key") == args.pair_key:
            now_iso = datetime.now().isoformat(timespec="seconds")
            r["outcome"] = args.outcome
            if args.outcome in RESOLVED_OUTCOMES:
                r["resolved_at_ingest"] = now_iso
            return i
    raise SystemExit(f"error: no row for pair_key={args.pair_key!r} to backfill")


def _backfill_cost(rows: list[dict], args: argparse.Namespace) -> int:
    """Stamp invocation-level cost fields onto every row whose batch_id matches
    args.batch_id. Only non-None args overwrite. Returns the number stamped."""
    cost = {
        "total_tokens": args.total_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
        "websearch_calls": args.websearch_calls,
        "context_tokens_post_pass": args.context_tokens_post,
        "output_tokens_pass": args.output_tokens_pass,
    }
    cost = {k: v for k, v in cost.items() if v is not None}
    n = 0
    for r in rows:
        if r.get("batch_id") == args.batch_id:
            r.update(cost)
            n += 1
    if n == 0:
        raise SystemExit(f"error: no rows with batch_id={args.batch_id!r} to backfill")
    return n


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    wiki_root = resolve_wiki_root(args.wiki)
    if wiki_root is None:
        return 2

    log_path = wiki_root / ".meta" / "contradiction_log.jsonl"

    if not args.backfill_cost and args.pair_key is None:
        print("error: --pair-key is required (omit only for --backfill-cost)", file=sys.stderr)
        return 2

    if args.backfill or args.backfill_ingest or args.backfill_cost:
        rows = read_rows(log_path)
        if not rows:
            print(f"error: {log_path} is empty — nothing to backfill", file=sys.stderr)
            return 2
        if args.backfill_cost:
            if args.batch_id is None:
                print("error: --backfill-cost requires --batch-id", file=sys.stderr)
                return 2
            n = _backfill_cost(rows, args)
            write_rows(log_path, rows)
            print(f"backfilled cost on {n} row(s) for batch_id={args.batch_id}")
        elif args.backfill:
            idx = _backfill_capture(rows, args)
            write_rows(log_path, rows)
            print(f"backfilled row {idx} for pair_key={args.pair_key} (action={args.user_action})")
        else:
            if args.outcome is None:
                print(
                    "error: --backfill-ingest requires --outcome a_wins|b_wins|both_stand|defer",
                    file=sys.stderr,
                )
                return 2
            idx = _backfill_ingest(rows, args)
            write_rows(log_path, rows)
            print(f"backfilled row {idx} outcome={args.outcome} for pair_key={args.pair_key}")
        return 0

    # Initial write — require pair identity
    missing = [
        f for f in ("pair_a_slug", "pair_a_type", "pair_b_slug", "pair_b_type", "shape")
        if getattr(args, f.replace("-", "_")) is None
    ]
    if missing:
        print(f"error: --{', --'.join(missing)} required on initial write", file=sys.stderr)
        return 2

    append_jsonl(log_path, build_row(args))

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
