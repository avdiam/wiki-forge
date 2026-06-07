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

Cost fields stay `null` in v1 — `/wiki-corroborate` is main-session
only (no subagent dispatched); per the session 42 finding,
`total_tokens` for main-session work isn't surfaced by the harness.
Reserved fields kept for forward symmetry with the other JSONL logs.

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
    # parallel-path captures (e.g., /wiki-triage) close the loop.
    python append_corroborate_log.py --wiki <wiki-root> --backfill-ingest \\
        --page-slug claude-managed-agents --decision Y
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
    p.add_argument("--decision", choices=sorted(VALID_DECISIONS), default=None,
                   help="Verify-clearing decision for --backfill-ingest. Y=clear; P=partial-narrow; N=retain.")
    # Initial-write fields
    p.add_argument("--page-slug", required=True)
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
    # Cost fields — all null in v1 (main-session only); reserved for future
    p.add_argument("--total-tokens", type=optional_int, default=None)
    p.add_argument("--tool-uses", type=optional_int, default=None)
    p.add_argument("--duration-ms", type=optional_int, default=None)
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
        "total_tokens": args.total_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
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
    close the corroborate-log loop. Returns row index."""
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("page_slug") == args.page_slug:
            now_iso = datetime.now().isoformat(timespec="seconds")
            r["flag_decision_at_ingest"] = args.decision
            if args.decision == "Y":
                r["flag_cleared_at_ingest"] = now_iso
            return i
    raise SystemExit(f"error: no row for page_slug={args.page_slug!r} to backfill")


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    wiki_root = resolve_wiki_root(args.wiki)
    if wiki_root is None:
        return 2

    log_path = wiki_root / ".meta" / "corroborate_log.jsonl"

    if args.backfill or args.backfill_ingest:
        rows = read_rows(log_path)
        if not rows:
            print(f"error: {log_path} is empty — nothing to backfill", file=sys.stderr)
            return 2
        if args.backfill:
            idx = _backfill_capture(rows, args)
            write_rows(log_path, rows)
            print(f"backfilled row {idx} for page_slug={args.page_slug} (action={args.user_action})")
        else:
            if args.decision is None:
                print("error: --backfill-ingest requires --decision Y|P|N", file=sys.stderr)
                return 2
            idx = _backfill_ingest(rows, args)
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
