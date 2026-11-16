# wiki-ingestor — `verify: true` decisions

> Moved out of the wiki-ingestor playbook (s137, script audit Wave 3) so ingests that don't hit this branch don't load it. The ingestor Reads it from the dispatch's `References:` directory when the branch fires. Every rule here is as binding as the playbook's.

## Step 3b: apply each decision

- **`verify: true` re-evaluation (closes the loop with `/wiki-corroborate`).** Inspect the dispatch prompt's `Verify-clearing decisions:` block for a line matching this page's slug. Apply the user's choice as part of the EDIT body, AND **track every slug+decision in working state** (Y, P, AND N) for the post-commit telemetry backfill in step 5 — every decision is audit-visible, not just Y. The backfill defers past the late-contradiction rollback boundary so a halt-and-rollback doesn't leave a phantom stamp against changes that never reached HEAD.
  - **Y (Clear).** Delete the inline `⚠ needs verification:` callout from the body with Edit (or its `> [!WARNING] needs verification:` blockquote variant — match either shape). Then add `--clear-verify` to this page's `fm_edit.py` call: it removes the `verify:` line and bumps `confidence:` one tier (e.g., 0.7 → 0.8), capped at 1.0. Don't also pass `--confidence +0.1` for the same page, or it moves two tiers.
  - **N (Retain).** Leave `verify: true` and the inline callout intact. New source still gets added to `sources:` and the `### From <source>` evidence subsection lands as usual; the flagged claim is just not yet considered corroborated.
  - **P (Partial: "<new text>").** Keep `verify: true`; rewrite the inline callout to use the user-supplied narrower text. The new callout body replaces everything after `⚠ needs verification:` (or the `> [!WARNING]` blockquote variant) up to the first blank line / next-block marker.
  - **Page not listed in the decisions block.** Treat as N (no decision = no change). Do not auto-clear under any circumstance. **Do not** include unlisted slugs in the telemetry backfill — only slugs with explicit Y/P/N decisions get logged.

  Skip this bullet entirely when the dispatch prompt's `Verify-clearing decisions:` is `none` — most ingests don't touch flagged pages.

## Step 4: post-commit telemetry backfill

Add one flag per tracked decision (Y, P, AND N — every decision is
audit-visible) to step 4's `ingest_commit.py` call:

```bash
--verify-decision <slug>=<Y|P|N>
```

After the commit, the script stamps each decision on the most recent
`corroborate_log.jsonl` row for that `page_slug`, whatever its
`user_action` state, so parallel-path captures (the source arrived via
`/wiki-triage`, not `/wiki-corroborate` → `/wiki-capture`) still close the
loop. `flag_decision_at_ingest` is always set; Y also stamps
`flag_cleared_at_ingest`. The script's status line reads
`Verify-clearing: <slug> — <decision> (stamped)`, or `(skipped (no
corroborate_log row))` when the user never ran `/wiki-corroborate` on the
slug: that is the normal-queue case, not a failure. Copy the line into
your report. An `ERROR` line is a real error: note it and continue.
Telemetry is observational, not a contract; firing post-commit avoids a
phantom stamp if a late contradiction rolled back the writes. No flags
when no verify-clearing decisions fired in this ingest.
