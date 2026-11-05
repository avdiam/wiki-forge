# Conditional takeaway sections (wiki-ingest step 11)

Insert these sections into the step-11 takeaway block, after
`### Proposed operations` and before `### Contradictions detected`, only
when their condition holds.

### verify: true re-evaluation
(only emitted when the proposed plan has EDIT operations on pages whose
frontmatter currently carries `verify: true` — closes the loop with
`/wiki-corroborate`. To detect, grep each EDIT target's frontmatter for
`^verify: true` once before assembling the takeaway block; emit the section
only if at least one EDIT target matches. Skip entirely otherwise — most
ingests don't touch flagged pages.)

For each EDIT page that currently has `verify: true`, surface one block:

- **[[<slug>]]** — currently flagged with callout: `<existing callout text, abbreviated to ~120 chars>`
- New source: [[<source-slug>]] (this ingest)
- Decision (per page, default ASK):
  - **(Y) Clear** — remove `verify: true` from frontmatter and delete the inline `⚠ needs verification:` callout. Confidence rises one tier (cap at 1.0); page enters normal cited-claim status.
  - **(N) Retain** — new source adds breadth but doesn't address the specific verification target. Flag and callout stay.
  - **(P) Partial** — narrow the callout to the still-unverified portion. Reply with the new callout text inline.

Default: ask. Never auto-clear. The new source may corroborate the flagged
claim, partially address it, or be unrelated — the user owns the call.

### Contradiction resolution
(only emitted when the proposed plan has EDIT operations on pages whose
frontmatter currently carries a non-empty `contradicts:` list — closes the
loop with `/wiki-resolve-contradictions`. To detect, parse each EDIT
target's frontmatter for a non-empty `contradicts:` list once before
assembling the takeaway block; for each match, look up the other side of
each pair via the canonical `<slug-a>|<slug-b>` pair_key (slugs sorted
ascending). Emit the section only if at least one such pair exists. Skip
entirely otherwise — most ingests don't touch pages with active
contradicts: edges.)

For each `contradicts:` pair surfaced by an EDIT target, present one block:

- **Pair:** [[<slug-a>]] ↕ [[<slug-b>]]
- **Disagreement:** `<callout_excerpt from either side, abbreviated to ~120 chars; or "no inline callout — structural edge only">`
- **New source:** [[<source-slug>]] (this ingest)
- **Outcome (per pair, default `defer`):**
  - **(a_wins)** — side A's claim stands per the new source. Ingestor adds `> [!NOTE] Resolved: <claim summary> stands per [[<source-slug>]]` to the aggregator page (the concept/entity that cites both sides; for source↔source pairs the aggregator is the concept/entity that already cites both — name it inline if ambiguous). Removes `[[<slug-b>]]` from side A's `contradicts:` frontmatter AND `[[<slug-a>]]` from side B's `contradicts:` frontmatter (symmetric edge removal).
  - **(b_wins)** — mirror of `a_wins` for side B.
  - **(both_stand)** — genuine disagreement preserved. Ingestor removes the symmetric edge from both `contradicts:` lists, adds a `## Related` entry on each side pointing to the other with the annotation `(disagreement examined — both positions stand; see [[<source-slug>]])`. No callout added.
  - **(defer)** — no edits. `contradicts:` edge stays in place. Useful when the new source touches the page but doesn't address the specific disagreement.

Default: `defer`. Never auto-resolve. The new source may adjudicate the
disputed claim, partially address it, or be unrelated to the
disagreement — the user owns the call. Multiple pairs on the same EDIT
page are decided independently (one outcome per pair).

`superseded_by:` is NOT used by this flow — that field is reserved for
its original entity-version use case (e.g., `gpt-3 → superseded_by:
[[gpt-4]]`). Evidential disagreements use the `> [!NOTE] Resolved:`
callout + edge-removal mechanic above.
