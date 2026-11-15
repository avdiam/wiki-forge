# wiki-ingestor — `verify: true` decisions

> Moved out of the wiki-ingestor playbook (s137, script audit Wave 3) so ingests that don't hit this branch don't load it. The ingestor Reads it from the dispatch's `References:` directory when the branch fires. Every rule here is as binding as the playbook's.

## Step 3b: apply each decision

- **`verify: true` re-evaluation (closes the loop with `/wiki-corroborate`).** Inspect the dispatch prompt's `Verify-clearing decisions:` block for a line matching this page's slug. Apply the user's choice as part of the EDIT body, AND **track every slug+decision in working state** (Y, P, AND N) for the post-commit telemetry backfill in step 5 — every decision is audit-visible, not just Y. The backfill defers past the late-contradiction rollback boundary so a halt-and-rollback doesn't leave a phantom stamp against changes that never reached HEAD.
  - **Y (Clear).** Remove the `verify: true` line from the page's frontmatter; delete the inline `⚠ needs verification:` callout from the body (or its `> [!WARNING] needs verification:` blockquote variant — match either shape). Bump `confidence:` one tier (e.g., 0.7 → 0.8), capped at 1.0.
  - **N (Retain).** Leave `verify: true` and the inline callout intact. New source still gets added to `sources:` and the `### From <source>` evidence subsection lands as usual; the flagged claim is just not yet considered corroborated.
  - **P (Partial: "<new text>").** Keep `verify: true`; rewrite the inline callout to use the user-supplied narrower text. The new callout body replaces everything after `⚠ needs verification:` (or the `> [!WARNING]` blockquote variant) up to the first blank line / next-block marker.
  - **Page not listed in the decisions block.** Treat as N (no decision = no change). Do not auto-clear under any circumstance. **Do not** include unlisted slugs in the telemetry backfill — only slugs with explicit Y/P/N decisions get logged.

  Skip this bullet entirely when the dispatch prompt's `Verify-clearing decisions:` is `none` — most ingests don't touch flagged pages.

## Step 5: post-commit telemetry backfill

**Post-commit verify-clearing telemetry backfill.** For each slug tracked
during step 3b's verify-clearing decision (Y, P, AND N — every decision is
audit-visible), stamp the decision on the most-recent matching
`corroborate_log.jsonl` row:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_corroborate_log.py" --wiki <wiki> --backfill-ingest \
  --page-slug <slug> --decision <Y|P|N>
```

The script targets the most recent row matching `page_slug` regardless of
`user_action` state — parallel-path captures (e.g., the user picked the
source via `/wiki-triage` instead of `/wiki-corroborate` → `/wiki-capture`)
still close the loop. The script always sets `flag_decision_at_ingest`;
on Y it additionally stamps `flag_cleared_at_ingest` with the current
timestamp. P and N leave `flag_cleared_at_ingest` null but the decision
field captures the action so per-decision telemetry analysis is possible.
Same shape as a telemetry repair: a row written before its fact was
known gets stamped afterwards, never by amending a commit. If no matching row exists (e.g., the user never ran
`/wiki-corroborate` on this slug — the source surfaced via the normal
ingest queue), the script prints `skipped: no corroborate_log row …` and
exits 0. That is the expected normal-queue case: don't report it as a
failure; one report line `Verify-clearing: <slug> — <decision>` is
enough. A non-zero exit is a real error: note it in the report and
continue. Telemetry is observational, not a contract; firing post-commit
avoids a phantom stamp if a late contradiction rolled back the writes.
Skip entirely when no verify-clearing decisions fired in this ingest.
