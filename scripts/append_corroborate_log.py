#!/usr/bin/env python3
"""Append one row to wiki/.meta/corroborate_log.jsonl.

Called by `/wiki-corroborate` (one row per flag searched, not per skill
invocation) and back-filled by the wiki-ingestor (sets `flag_cleared_at_ingest`
on Y at the verify-clearing takeaway gate).

Row schema (one row per `verify: true` flag the skill searched; mutates
twice after creation — once at `/wiki-capture` time, once at
`/wiki-ingest` time):
    {
      "timestamp": "2026-05-15T14:30:21",
      "page_slug": "claude-managed-agents",
      "page_type": "entity",
      "page_bucket": "B",
      "callout_text": "the 100KB persistent-memory cap and...",
      "candidates_returned": 5,
      "candidates_by_tier": {"tier_1": 2, "tier_2": 2, "tier_3": 1},
      "candidate_domains": [
        {"domain": "docs.anthropic.com/claude", "tier": "tier_1"},
        ...
      ],
      "include_tier_3_flag": false,
      "user_picked": "https://docs.anthropic.com/...",
      "user_action": "captured",
      "captured_to_vault": "vault/Web Articles/2026-05-15-...",
      "flag_cleared_at_ingest": "2026-05-15T14:55:11",
      "flag_decision_at_ingest": "Y",
      "total_tokens": null, "tool_uses": null, "duration_ms": null
    }

`page_bucket` values follow the 2026-05-08 queue audit:
  - A — entity bio thinness (`category: ai-practitioners`)
  - B — entity with specific factual claims
  - C — concept / comparison / synthesis
  - D — source (out of scope for /wiki-corroborate; reserved for
        hypothetical downstream callers)

`user_action` values:
  - `deferred`  — initial state; skill surfaced candidates, no action yet
  - `captured`  — user ran /wiki-capture on the picked URL (backfilled)
  - `skipped`   — user explicitly passed on this batch (reserved)
`user_picked` and `captured_to_vault` populated only in `captured`.

`candidate_domains` (v1.2) — list of `{domain, tier}` records, one per
filtered candidate (post-filter, i.e. denylist dropped and `tier_3`
dropped unless `--include-tier-3`). Domains are host + first path
segment when present (`github.com/anthropic`, `linkedin.com/in/<user>`)
so path-prefix tier patterns stay distinguishable in analysis. Empty
list `[]` means WebSearch failed or every candidate was dropped; `null`
means the row predates the field. Purpose: seed-list backfill —
grouping `unclassified` domains across many rows surfaces real-world
host patterns the `url_classifier_lists.json` seeds miss. Privacy
posture: domain only; path tail / query / fragment dropped.

`flag_decision_at_ingest` is backfilled by the wiki-ingestor on every
verify-clearing decision at the takeaway-approval gate — captures the
user's choice so per-decision telemetry analysis is possible:
  - `"Y"`  — clear
  - `"P"`  — partial-narrow
  - `"N"`  — retain
  - `null` — decision hasn't fired yet, or no row exists for the page
Matches by `page_slug` + most-recent row regardless of `user_action`
state, so parallel-path captures (e.g., via /wiki-triage) still close
the loop.

`flag_cleared_at_ingest` is the dependent stamp: set to ISO timestamp
only on Y (full clear); left `null` on P / N / no-decision.
Invariant: `flag_cleared_at_ingest != null` ⟺ `flag_decision_at_ingest == "Y"`.

Cost fields (`total_tokens`, `tool_uses`, `duration_ms`, `websearch_calls`,
`context_tokens_post_pass`, `context_tokens_pre_pass`, `context_growth`,
`output_tokens_pass`, `flags_in_batch`) were `null` in v1 — the harness emits
no `<usage>` block for main-session (non-subagent) work, so there was no live
surface to read. v2 (2026-06-07) recovers them *after the fact* from the Claude
Code **session transcript JSONL** via `session_cost_probe.py`, which aggregates
per-turn `usage` over the invocation's line-window. The `/wiki-corroborate` (and
`/wiki-resolve-contradictions`) skill records the transcript line count at
the start of its search loop, tags every per-flag row with a `--batch-id`,
then runs the probe and `--backfill-cost --batch-id <id>` to stamp the
invocation-level cost onto all rows of the batch. This is the telemetry the
dev/tech-debt.md "execution shape — inline vs subagent" entry needs to answer
the fork-vs-inline ROI question.

**Batch-unit convention (decided 2026-06-08).** The unit of measurement is the
**invocation (batch)**; every row carries `flags_in_batch` (= the number of
flags the invocation drained) so analysis can normalize where the metric
demands it. Two cost questions, two units:
  - *Context discipline* — the load-bearing fork justification ("does running
    this inline bloat the main session?") is measured **per-invocation** by
    `context_growth` (= post − pre) and is NOT normalized: a fork helps exactly
    when one invocation inflates resident context, regardless of how many flags
    it covered. `context_growth` supersedes the absolute `context_tokens_post_pass`
    as the gate metric — the absolute is contaminated by how full the session
    already was when the pass ran (the session-95 ralphy warmup taint), the
    delta is not.
  - *Throughput cost* — `total_tokens` / `output_tokens_pass` scale with batch
    size, so cross-invocation comparison divides by `flags_in_batch` (per-flag
    cost). `websearch_calls` is ≈1/flag, i.e. batch size in disguise, so it is
    retained as descriptive but is no longer a standalone ROI gate.

`batch_id` groups the per-flag rows of one invocation so per-invocation
aggregation (medians) is computable — without it the per-flag rows have no
invocation key. `skill_version` records which skill revision produced the batch.

Usage:
    # Initial write — one row per flag searched (user_action defaults to deferred).
    python append_corroborate_log.py --wiki <wiki-root> \\
        --page-slug claude-managed-agents --page-type entity \\
        --page-bucket B --callout-text "the 100KB persistent-memory cap..." \\
        --candidates-returned 5 \\
        --candidates-by-tier '{"tier_1":2,"tier_2":2,"tier_3":1}' \\
        --include-tier-3-flag false \\
        --user-action deferred

    # Backfill on capture — flips action and records the captured vault path.
    python append_corroborate_log.py --wiki <wiki-root> --backfill \\
        --page-slug claude-managed-agents \\
        --user-picked "https://docs.anthropic.com/..." \\
        --user-action captured \\
        --captured-to-vault "vault/Web Articles/2026-05-15-..."

    # Backfill at ingest — verify-clearing decision on the most recent row
    # matching --page-slug. Decision is one of Y|P|N. On Y, stamps
    # flag_cleared_at_ingest with the current timestamp; on P/N leaves it
    # null. flag_decision_at_ingest is always set so N/P decisions are
    # audit-visible too. Matches deferred rows as well as captured rows so
    # parallel-path captures (e.g., /wiki-triage) close the loop. No matching
    # row (page never searched by /wiki-corroborate) prints "skipped: ..."
    # and exits 0 — the normal-queue case, not an error.
    python append_corroborate_log.py --wiki <wiki-root> --backfill-ingest \\
        --page-slug claude-managed-agents --decision Y

    # Backfill cost — stamp invocation-level cost (from session_cost_probe.py)
    # onto every row sharing --batch-id. Run once per /wiki-corroborate
    # invocation, after the search loop closes.
    python append_corroborate_log.py --wiki <wiki-root> --backfill-cost \\
        --batch-id 9803eda1-940 --total-tokens 41200 --tool-uses 7 \\
        --duration-ms 83100 --websearch-calls 6 \\
        --context-tokens-post 96800 --context-tokens-pre 85000 \\
        --context-growth 11800 --output-tokens-pass 5100
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
VALID_PAGE_BUCKETS = {"A", "B", "C", "D"}
VALID_PAGE_TYPES = {"concept", "entity", "comparison", "synthesis"}
VALID_DECISIONS = {"Y", "P", "N"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--backfill", action="store_true",
                      help="Mutate the most recent row matching --page-slug (and user_action=deferred unless --user-picked given) — sets user_picked / user_action / captured_to_vault.")
    mode.add_argument("--backfill-ingest", action="store_true",
                      help="Stamp the verify-clearing decision on the most recent row matching --page-slug. Requires --decision Y|P|N. On Y sets flag_cleared_at_ingest=now; on P/N leaves it null. Always sets flag_decision_at_ingest. Matches any user_action so parallel-path captures (e.g., /wiki-triage) close the loop.")
    mode.add_argument("--backfill-cost", action="store_true",
                      help="Stamp invocation-level cost fields (from session_cost_probe.py) onto every row matching --batch-id. Run once per /wiki-corroborate invocation after the search loop closes.")
    p.add_argument("--decision", choices=sorted(VALID_DECISIONS), default=None,
                   help="Verify-clearing decision for --backfill-ingest. Y=clear; P=partial-narrow; N=retain.")
    # Initial-write fields
    p.add_argument("--page-slug", default=None,
                   help="Required for initial write / --backfill / --backfill-ingest; unused by --backfill-cost (which matches on --batch-id).")
    p.add_argument("--page-type", choices=sorted(VALID_PAGE_TYPES), default=None)
    p.add_argument("--page-bucket", choices=sorted(VALID_PAGE_BUCKETS), default=None)
    p.add_argument("--callout-text", default=None)
    p.add_argument("--candidates-returned", type=optional_int, default=None)
    p.add_argument("--candidates-by-tier", type=candidates_dict, default={})
    p.add_argument("--candidate-urls", type=candidate_urls, default=None,
                   help="JSON array of {url, tier} records (one per filtered candidate). Hosts are extracted and stored as candidate_domains: [{domain, tier}, ...]. Pass '[]' for genuinely-zero-candidate runs; omit entirely on backfill operations.")
    p.add_argument("--include-tier-3-flag", type=optional_bool, default=False)
    # Action fields
    p.add_argument("--user-picked", type=optional_str, default=None,
                   help="URL the user chose at the candidate-review gate")
    p.add_argument("--user-action", choices=sorted(VALID_USER_ACTIONS), default="deferred")
    p.add_argument("--captured-to-vault", type=optional_str, default=None,
                   help="Vault path the picked URL was captured to via /wiki-capture")
    # Invocation grouping (v2) — links the per-flag rows of one /wiki-corroborate
    # run so per-invocation cost aggregation (medians) is computable.
    p.add_argument("--batch-id", type=optional_str, default=None,
                   help="Invocation id shared by every per-flag row of one run (e.g. <session-short>-<start-line>). Required on --backfill-cost.")
    p.add_argument("--skill-version", type=optional_str, default=None,
                   help="Skill revision that produced the row/batch.")
    # Cost fields — v2 backfilled from session_cost_probe.py (see module docstring).
    p.add_argument("--total-tokens", type=optional_int, default=None)
    p.add_argument("--tool-uses", type=optional_int, default=None)
    p.add_argument("--duration-ms", type=optional_int, default=None)
    p.add_argument("--websearch-calls", type=optional_int, default=None,
                   help="server_tool_use.web_search_requests summed over the invocation window.")
    p.add_argument("--context-tokens-post", type=optional_int, default=None,
                   help="Main-session context size (input+cache_read+cache_creation) at the last turn of the pass.")
    p.add_argument("--context-tokens-pre", type=optional_int, default=None,
                   help="Main-session context size at the last turn BEFORE the pass (the window's pre-anchor).")
    p.add_argument("--context-growth", type=optional_int, default=None,
                   help="context_tokens_post_pass - context_tokens_pre_pass: how much this invocation inflated main-session context. The load-bearing per-invocation fork-decision signal — absolute post is contaminated by prior session fill (the session-95 warmup taint), growth is not.")
    p.add_argument("--output-tokens-pass", type=optional_int, default=None,
                   help="output_tokens summed over the invocation window.")
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "page_slug": args.page_slug,
        "page_type": args.page_type,
        "page_bucket": args.page_bucket,
        "callout_text": args.callout_text,
        "candidates_returned": args.candidates_returned,
        "candidates_by_tier": args.candidates_by_tier,
        "candidate_domains": args.candidate_urls,
        "include_tier_3_flag": args.include_tier_3_flag,
        "user_picked": args.user_picked,
        "user_action": args.user_action,
        "captured_to_vault": args.captured_to_vault,
        "flag_cleared_at_ingest": None,
        "flag_decision_at_ingest": None,
        "batch_id": args.batch_id,
        "skill_version": args.skill_version,
        "total_tokens": args.total_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
        "websearch_calls": args.websearch_calls,
        "context_tokens_post_pass": args.context_tokens_post,
        "context_tokens_pre_pass": args.context_tokens_pre,
        "context_growth": args.context_growth,
        "output_tokens_pass": args.output_tokens_pass,
        "flags_in_batch": None,
    }


def _backfill_capture(rows: list[dict], args: argparse.Namespace) -> int:
    """Set user_picked / user_action / captured_to_vault on the most recent
    row matching page_slug AND user_action='deferred'. Returns row index."""
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("page_slug") == args.page_slug and r.get("user_action") == "deferred":
            if args.user_picked is not None:
                r["user_picked"] = args.user_picked
            r["user_action"] = args.user_action
            if args.captured_to_vault is not None:
                r["captured_to_vault"] = args.captured_to_vault
            return i
    raise SystemExit(f"error: no deferred row for page_slug={args.page_slug!r} to backfill")


def _backfill_ingest(rows: list[dict], args: argparse.Namespace) -> int:
    """Stamp the verify-clearing decision on the most recent row matching
    page_slug. flag_decision_at_ingest is always set (Y/P/N);
    flag_cleared_at_ingest is set only on Y. Matches any user_action so
    parallel-path captures (e.g., the user picked the source via
    /wiki-triage instead of /wiki-corroborate -> /wiki-capture) still
    close the corroborate-log loop. Returns row index, or None when the page
    was never searched by /wiki-corroborate (the common normal-queue case:
    nothing to stamp, not an error)."""
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("page_slug") == args.page_slug:
            now_iso = datetime.now().isoformat(timespec="seconds")
            r["flag_decision_at_ingest"] = args.decision
            if args.decision == "Y":
                r["flag_cleared_at_ingest"] = now_iso
            return i
    return None


def _backfill_cost(rows: list[dict], args: argparse.Namespace) -> int:
    """Stamp invocation-level cost fields onto every row whose batch_id matches
    args.batch_id. Only non-None cost args overwrite (so a partial probe can't
    wipe a field). `flags_in_batch` is ALWAYS stamped = the number of matched
    rows (the batch size) — it's the per-flag normalization denominator that
    makes throughput cost comparable across uneven batches, and it's always
    knowable at backfill time regardless of which probe fields are present.
    Returns the number of rows stamped."""
    cost = {
        "total_tokens": args.total_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
        "websearch_calls": args.websearch_calls,
        "context_tokens_post_pass": args.context_tokens_post,
        "context_tokens_pre_pass": args.context_tokens_pre,
        "context_growth": args.context_growth,
        "output_tokens_pass": args.output_tokens_pass,
    }
    cost = {k: v for k, v in cost.items() if v is not None}
    matched = [r for r in rows if r.get("batch_id") == args.batch_id]
    if not matched:
        raise SystemExit(f"error: no rows with batch_id={args.batch_id!r} to backfill")
    cost["flags_in_batch"] = len(matched)
    for r in matched:
        r.update(cost)
    return len(matched)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    wiki_root = resolve_wiki_root(args.wiki)
    if wiki_root is None:
        return 2

    log_path = wiki_root / ".meta" / "corroborate_log.jsonl"

    needs_slug = not args.backfill_cost
    if needs_slug and args.page_slug is None:
        print("error: --page-slug is required (omit only for --backfill-cost)", file=sys.stderr)
        return 2

    if args.backfill or args.backfill_ingest or args.backfill_cost:
        rows = read_rows(log_path)
        if not rows and args.backfill_ingest:
            # s138: a normal-queue ingest has no corroborate row to stamp; exit 0
            # so the ingestor doesn't report an expected miss as a failure.
            print(f"skipped: no corroborate_log row for page_slug={args.page_slug} (nothing to stamp)")
            return 0
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
            print(f"backfilled row {idx} for page_slug={args.page_slug} (action={args.user_action})")
        else:
            if args.decision is None:
                print("error: --backfill-ingest requires --decision Y|P|N", file=sys.stderr)
                return 2
            idx = _backfill_ingest(rows, args)
            if idx is None:
                print(f"skipped: no corroborate_log row for page_slug={args.page_slug} (nothing to stamp)")
                return 0
            write_rows(log_path, rows)
            print(f"backfilled row {idx} decision={args.decision} for page_slug={args.page_slug}")
        return 0

    # Initial write — require the schema-shape fields
    missing = [f for f in ("page_type", "page_bucket") if getattr(args, f.replace("-", "_")) is None]
    if missing:
        print(f"error: --{', --'.join(missing)} required on initial write", file=sys.stderr)
        return 2

    append_jsonl(log_path, build_row(args))

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
