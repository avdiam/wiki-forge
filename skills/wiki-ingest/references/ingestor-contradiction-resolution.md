# wiki-ingestor — contradiction-resolution decisions

> Moved out of the wiki-ingestor playbook (s137, script audit Wave 3) so ingests that don't hit this branch don't load it. The ingestor Reads it from the dispatch's `References:` directory when the branch fires. Every rule here is as binding as the playbook's.

## Step 3b: apply each outcome

- **Contradiction resolution (closes the loop with `/wiki-resolve-contradictions`).** Inspect the dispatch prompt's `Contradiction-resolution decisions:` block for any line whose `<slug-a>|<slug-b>` pair_key touches this page. Apply the user's outcome as part of the EDIT body, AND **track every pair_key+outcome in working state** (a_wins, b_wins, both_stand, AND defer — every decision is audit-visible) for the post-commit telemetry backfill in step 5.

  - **a_wins / b_wins.** Identify the **aggregator page** that should carry the Resolved callout: (i) if the dispatch line has an explicit `[aggregator: <slug>]` hint, use that page; (ii) otherwise, prefer the concept / entity / comparison side of the pair (source pages are not aggregators); (iii) for source↔source pairs without a hint, the aggregator is the concept/entity page already cited by both — DO NOT guess. The ingestor cannot ask the user mid-execution (it runs in a fork and only communicates via the final report). When the aggregator is ambiguous, **halt this pair's resolution only** (other pairs proceed normally): leave both `contradicts:` lists untouched, do NOT add a Resolved callout, and surface the ambiguity in the final structured report under an `Unresolved pairs:` line per pair, naming both candidate aggregators if more than one concept/entity cites both sources. The user re-dispatches with an explicit `[aggregator:]` hint on the next ingest touching either side. On the aggregator (when unambiguous), append a `> [!NOTE] Resolved: <one-sentence claim summary> stands per [[<source-slug>]] (this ingest).` callout near the existing `## Contradictions` section (or the disputed claim's evidence subsection if no `## Contradictions` heading exists). Then remove the edge from both sides in one call: `fm_edit.py <wiki> <winning-slug> --uncontradict <losing-slug>` (symmetric; an emptied `contradicts:` field is dropped rather than left as `contradicts: []`, which keeps the lint surface clean).
  - **both_stand.** Remove the symmetric edge with the same `fm_edit.py … --uncontradict` call. On each side, add a `## Related` entry pointing to the other side with the annotation `(disagreement examined — both positions stand; see [[<source-slug>]])`. If `## Related` doesn't exist on a page, create it as a new top-level section near the page's end. No Resolved callout is added — NPOV's "describe, don't resolve" frame.
  - **defer.** No edits to either side. The pair_key+outcome still gets tracked for telemetry — defer is an explicit decision, not a no-op.
  - **Pair not listed in the decisions block.** Treat as defer (no decision = no change). Do not auto-resolve under any circumstance. **Do** include unlisted pairs touched by this ingest in the telemetry backfill with outcome=`defer` — surface the implicit defer so analysis can distinguish "user actively deferred" from "pair was never surfaced." (Caveat: if no `### Contradiction resolution` block was emitted in the takeaway, there are no implicit-defer pairs to track — skip telemetry entirely.)

  `superseded_by:` is NOT used by this flow. It remains reserved for its original entity-version use case (`gpt-3 → superseded_by: [[gpt-4]]`); evidential disagreements use the callout + edge-removal mechanic above.

  Skip this bullet entirely when the dispatch prompt's `Contradiction-resolution decisions:` is `none` — most ingests don't touch pages with active `contradicts:` edges.

## Step 4: post-commit telemetry backfill

Add one flag per tracked `pair_key`+`outcome` (a_wins, b_wins, both_stand,
AND defer — every decision is audit-visible) to step 4's
`ingest_commit.py` call:

```bash
--contradiction-outcome "<slug-a>|<slug-b>=<a_wins|b_wins|both_stand|defer>"
```

The script sorts the pair key and, after the commit, stamps the outcome on
the most recent matching `contradiction_log.jsonl` row, whatever its
`user_action` state (manual-edit paths with no `/wiki-capture` roundtrip
still close the loop). `outcome` is always set; a_wins / b_wins /
both_stand also stamp `resolved_at_ingest`. A `(not stamped: …)` status
line means no row matched (the user never ran
`/wiki-resolve-contradictions` on this pair; the ingest merely touched a
page with an existing edge): note it in the report and continue.
Rollback protection is structural: step 4 is post-commit, so a
late-contradiction rollback never reaches the backfill — no phantom stamps
against changes that never reached HEAD. No flags when no
contradiction-resolution decisions fired in this ingest.
