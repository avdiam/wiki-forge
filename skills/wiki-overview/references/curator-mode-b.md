# wiki-curator — Mode B (full rewrite)

> Moved out of the curator playbook (s137, script audit Wave 3): soft runs, the common case, no longer load it. The `/wiki-overview --full` dispatch passes this file's absolute path as `Mode B instructions:`. Every rule here is as binding as the playbook's.

## Mode B — `full-start` (Pass 1 of full rewrite)

You are doing a wiki-wide synthesis. The result lands in a checkpoint file,
not in `overview.md` directly — Pass 2 (after user approval) does the
write.

### Read horizon (wider than soft)

Read, in this order:

1. `<wiki>/overview.md` — the current overview, in full. This is the
   continuity baseline; every section, pull-quote, and theme in here is
   evidence the wiki has cared about something.
2. `graph_analyzer.py` JSON output — top-15 hubs by inbound, orphans,
   components, typed edges. Topology only (slug + counts), not page bodies:
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/graph_analyzer.py" <wiki> --json --top 15
   ```
3. `<wiki>/log.md` — last 10 ingest entries. Use full lifetime counter, not
   since-last-lint.
4. The top-15 hub pages themselves, in full. (Step 2 gave you the slug
   list; this step reads what each hub *contains*. Both reads are
   intentional: topology + content.)
5. **5 sampled older pages** — pick 5 pages with `last_updated:` more than
   60 days old that aren't in the top-15 hubs. These probe whether the
   wiki has dropped older threads silently.
6. Anything the current overview cites in its pull-quotes (the source
   pages those quotes come from) so the preservation rule is enforceable.

### Continuity contract

You are NOT writing on a blank page. You are proposing a successor to the
current `overview.md`. For every section in the current overview, decide
one of:

- **Keep** verbatim or with minor refinement.
- **Update** with a written one-sentence delta (what's new, why it changed).
- **Drop** with a written one-sentence justification.

For every pull-quote in the current overview, decide:

- **Keep** verbatim.
- **Replace with** a stronger pull-quote, naming the new quote, its source
  page, and one sentence on why the old quote is no longer load-bearing.
- **Drop** with a written justification — and this is the hard rule:

> **HARD pull-quote preservation rule.** A pull-quote in the current
> overview is mission-statement-grade evidence that the wiki has cared
> about a framing. You may drop it ONLY if you can name *what supersedes
> it* (a stronger frame from a higher-confidence source, an empirically
> resolved debate, or an explicit user-deprecated theme). "Recency
> preference" is NOT a justification. "The latest ingest doesn't quote
> it" is NOT a justification. The Karhade `"compound, not evaporate"`
> incident — silently dropped between ingests #25 and #89 with no
> rationale — is exactly the failure mode this rule prevents.

### Draft the proposed rewrite

Compose the full successor `overview.md` as a string. Honor the continuity
contract — every drop carries its justification.

### Build the section-by-section diff

Produce a markdown diff document (this is what the user reviews):

```markdown
## Proposed overview rewrite

### Pull-quotes
- **Keep:** "<quote>" — <attribution>
- **Replace with:** "<new quote>" — <new attribution>. Old: "<old quote>" — <old attribution>. Why: <one sentence>.
- **Drop:** "<quote>" — <attribution>. Why: <one sentence>.

### Section: ## Current state
- **Status:** keep / update / drop
- **Delta (if update):** <one sentence>
- **Justification (if drop):** <one sentence>

### Section: ## Cluster map
... (same shape per section)

(Repeat for every section in the current overview.)

### New sections proposed
- `## <header>` — <one-sentence rationale>
- (or "None.")
```

### Write the checkpoint

Create `.claude/tmp/` if missing. Write
`.claude/tmp/wiki-curator-checkpoint.json` containing:

```json
{
  "agent": "wiki-curator",
  "wiki_root": "<absolute path>",
  "trigger": "<verbatim trigger string from dispatch>",
  "proposed_overview": "<full text of the proposed successor overview.md>",
  "diff_summary": "<the section-by-section diff doc above, verbatim>",
  "current_overview_path": "<wiki>/overview.md",
  "facts_loaded": {
    "top_hubs": [{"slug": "...", "inbound": N}, ...],
    "ingests_in_window": [<lifetime ingest numbers covered>],
    "older_pages_sampled": ["<slug>", ...]
  },
  "next_step": "Read user's reply (approve / reject / edit X / replace section Y with Z); if approve, write proposed_overview to current_overview_path verbatim; if edit instructions provided, apply them surgically and write."
}
```

Mode is implicit from the dispatch envelope — `full-resume` is the only mode that reads the checkpoint, so a `mode_origin` field would be redundant.

The checkpoint is the contract between Pass 1 and Pass 2. It must contain
**every fact Pass 2 needs** to apply changes without re-reading the source
material. Do not stash raw page content in it (Pass 2 trusts the
proposed_overview as ground truth); do stash the structured analysis.

### Return paused

Return:

```json
{
  "status": "paused",
  "checkpoint_file": ".claude/tmp/wiki-curator-checkpoint.json",
  "display": "<the full diff_summary markdown — shown verbatim to the user by the dispatching skill>"
}
```

The skill will print `display` verbatim to the user, collect their reply,
and dispatch you again with `mode: full-resume`.

### Mode B — Pass 1 rules

- Write **nothing** to `wiki/overview.md` in Pass 1.
- The checkpoint is the only file written in Pass 1.
- No commits.
- Honor the HARD pull-quote preservation rule. If the current overview has
  N pull-quotes and your proposed overview drops M, you must have M
  written justifications.
- **Intro-phrase anchor (HARD).** The `proposed_overview` you stash in the
  checkpoint MUST contain the literal phrase `Last full rewrite at ingest
  #<N> (<YYYY-MM-DD>)` somewhere in its intro paragraph, substituting the
  `Current ingest count` and `Today's date` from the dispatch prompt. This
  phrase is the machine-readable anchor that `${CLAUDE_PLUGIN_ROOT}/scripts/drill_state.py`
  keys on to compute the welcome report's `Overview:` freshness field —
  without it, every session start after a Mode B rewrite falsely reports
  the overview as stale by the full lifetime ingest count. Phrasing of the
  surrounding intro is yours to shape; the literal `Last full rewrite at
  ingest #N (YYYY-MM-DD)` substring is the contract.

---

## Mode B — `full-resume` (Pass 2 of full rewrite)

### Steps

1. Read the checkpoint at the path provided in the dispatch prompt
   (`.claude/tmp/wiki-curator-checkpoint.json`). Treat its contents as
   authoritative — do NOT re-read source pages, the current overview, or
   any other context. Pass 1 already loaded what was needed.
2. Parse the user's reply (`User response:` from the dispatch prompt).
   Recognize these shapes:
   - **Approve** (`approve`, `yes`, `ship it`, `looks good`) → write
     `proposed_overview` to `<wiki>/overview.md` verbatim.
   - **Reject** (`reject`, `no`, `abort`, `cancel`) → do nothing; delete
     checkpoint; return done with status `cancelled`.
   - **Edit instructions** (free-form: "drop section X", "keep the Karhade
     quote", "replace the Cluster map section with Y", "approve everything
     except section Z") → apply the user's instructions surgically to
     `proposed_overview`, then write.
   - **Ambiguous** — if the reply doesn't clearly map to one of the above,
     return another `paused` with `display` asking for clarification
     rather than guessing.
3. After writing, run:
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/check_overview_triggers.py" <wiki> --init --force
   ```
   This re-baselines the trigger config to the new state — without it,
   the same triggers would fire again immediately.
4. Delete the checkpoint:
   ```bash
   rm -f .claude/tmp/wiki-curator-checkpoint.json
   ```
   Leave `.claude/tmp/` itself in place (it is gitignored); one plain command,
   no chaining.
5. Do NOT commit — the `/wiki-overview` skill commits upstream.
6. Return:

   ```json
   {
     "status": "done",
     "result": "Mode B rewrite applied. Sections changed: <count>. Pull-quotes: <kept K, replaced R, dropped D>. Baseline reset via --init."
   }
   ```

   For `cancelled`:

   ```json
   {
     "status": "done",
     "result": "Mode B rewrite cancelled by user. overview.md unchanged. Checkpoint deleted."
   }
   ```

### Mode B — Pass 2 rules

- Trust the checkpoint. No re-reading of original sources unless the
  user's reply explicitly demands a re-derivation.
- Surgical edits only when user provides edit instructions — preserve
  every other byte of `proposed_overview`.
- Always re-init the trigger baseline on a successful write — failure to
  do so causes infinite trigger-fires.
- Always delete the checkpoint on a terminal status (done / cancelled).
