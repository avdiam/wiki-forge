---
name: wiki-triage
description: Scan the vault for unprocessed files, pre-screen each for relevance and quality, propose ingest batches and skip decisions, and write a triage file as the session-to-session handoff. Use when the user runs /wiki-triage, says "triage the backlog", "what's pending", "plan the next ingests", or when >5 unprocessed vault files accumulate.
---

# wiki-triage (backlog planning)

Semi-automated triage: script scans and pre-screens, LLM proposes batches
and skip decisions, user approves and adjusts, skill writes the handoff file.

## When to trigger

- User runs `/wiki-triage`
- User asks "what's pending", "plan the next ingests", "triage the backlog"
- During `/wiki-ingest all new` when >5 unprocessed files are found — suggest
  triage first instead of sequential ingest
- Proactively at session start if the wake-up drill reveals >10 untracked
  vault files (mention it in the welcome report, don't auto-run)

## Workflow

### 1. Run the triage scanner

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/triage_backlog.py" --wiki <wiki> --vault <vault> --json
```

This returns a JSON report with:
- Total vault files, already-processed count, pending count
- Per-file records: slug, title, author, tags, word count, pre-screen verdict
  (`pass` / `warn` / `auto-skip`), reasons, tag overlap, domain score, promo signals

If pending count is 0: report "Vault is fully triaged — nothing pending." and stop.

### 2. Read existing triage file (if any)

Check for `dev/ingest-triage-*.md`. If one exists and has unchecked batches,
present the user with a choice:
- **Continue** the existing triage (don't regenerate — just show status)
- **Replace** it with a fresh triage (archive the old one first)
- **Merge** new files into the existing triage

### 3. Propose skip decisions

For each file with verdict `auto-skip` or `warn`:

- **`auto-skip`** files: propose a skip reason (`off-domain`, `promotional`,
  `shallow`) and the stub that would be written to `vault/.skipped/`.
- **`warn`** files: present the warning reasons and ask the user to decide:
  ingest, skip, or defer (leave for next triage).

Present as a table:

```markdown
## Proposed skips

| # | File | Verdict | Reason | Proposed action |
|---|------|---------|--------|-----------------|
| 1 | slug | auto-skip | <reason> | skip (off-domain) |
| 2 | slug | warn | <reason> | ? — your call |
```

Wait for user to confirm/adjust skip decisions before proceeding to batching.

### 4. Propose ingest batches

For the remaining `pass` files (and any `warn` files the user chose to ingest):

1. **Read each file's frontmatter** (title, author, tags, published date) —
   already in the triage JSON, no extra reads needed.
2. **Group into batches of 5** based on thematic coherence:
   - Cluster by overlapping tags and topic affinity
   - Place articles by the same author adjacently when possible
   - Order dependencies: if article B builds on article A's entity, A goes first
   - Name each batch with a short thematic label (e.g., "verification + comparisons")
3. **Within each batch, order matters:**
   - Entity-creating articles before articles that reference those entities
   - Broader taxonomy articles before narrower ones
   - Listicles/surveys early (they establish category structure)

Present as:

```markdown
## Proposed batches

### Batch 1 — <thematic label>
**Rationale:** <one sentence on why these go together>

- [ ] **`<slug>.md`** (<author>) — <one-line what it contributes>
- [ ] **`<slug>.md`** (<author>) — <one-line what it contributes>
...

### Batch 2 — <thematic label>
...

### Maybe (decide at takeaway pass)
| # | File | Decision trigger |
|---|------|-----------------|
| 1 | slug | <when to ingest vs skip> |
```

Wait for the user to approve, reorder, or adjust batches.

### 5. Execute skip decisions

For each confirmed skip, write the stub and log it:

```bash
# Write stub
cat > vault/.skipped/<slug>.md << 'EOF'
---
url: <from original frontmatter>
skipped_on: <today>
reason: <off-domain | promotional | shallow | already-folded>
folded_into: <wiki-page-slug if applicable>
source_tags: <from original>
---
EOF

# Log it
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" note \
  --title "skipped: <slug>" \
  --note "reason: <reason> (triage)" \
  --wiki <wiki>
```

### 6. Write the triage file

Write `dev/ingest-triage-<YYYY-MM-DD>.md` with this structure:

```markdown
---
title: "Ingest backlog triage — <date>"
status: active
created: <date>
---

# Ingest backlog triage — <date>

<N> unprocessed vault files triaged. **<X> ingest** (<Y> batches),
**<Z> skip**, **<W> maybe**.

## Status checklist

- [x] **Skips (<Z>)** moved to vault/.skipped/ _(this session)_
- [ ] **Batch 1** — <N> ingests (<thematic label>)
- [ ] **Batch 2** — <N> ingests (<thematic label>)
...
- [ ] **Maybes (<W>)** decided one way or the other

## Batching constraint

5 articles per session, one at a time. Sequential dispatches only.

---

## Skips (<Z> — done)

| # | File | reason | folded_into |
|---|------|--------|-------------|
...

## Batch 1 — <thematic label>

**Rationale:** ...

- [ ] **`<slug>.md`** (<author>) — ...
- [ ] **`<slug>.md`** (<author>) — ...
...

## Batch N — ...

## Maybes (<W> — decide at takeaway pass)

| # | File | Decision trigger |
|---|------|-----------------|
...

---

## Session protocol

- Dispatch one `/wiki-ingest` per article, sequentially.
- `/wiki-ingest` ticks each article's checkbox (`- [ ]` → `- [x]`) as it
  commits, and ticks the batch box in the Status checklist when the batch's
  last article lands. A mid-batch stop therefore leaves an accurate record
  of what's done — the next session's wake-up drill points "Batch X next /
  First up" at the first *unticked* article, not at article #1.
- Mechanical lint runs after every ingest (in-agent).
- After every 10 ingests, `/wiki-lint` chains into `/wiki-overview --soft`.
- Out-of-cycle full lint if any single ingest surfaces ≥3 contradictions.

## Cleanup trigger

When all checkboxes are ticked:
1. Delete this file.
2. Delete the matching tech-debt pointer (if any).
3. Mention completion in the session narrative (if warranted).

## Provenance

Triaged in session <N> (<date>) via /wiki-triage.
Pre-screened by ${CLAUDE_PLUGIN_ROOT}/scripts/triage_backlog.py + ${CLAUDE_PLUGIN_ROOT}/scripts/pre_screen.py.
```

### 7. Regenerate the vault URL index

Triage has just walked every content folder (`vault/Web Articles/`, `vault/Clippings/`, …) + `vault/.skipped/` (and may
have just written new skip stubs in step 5) — exactly the moment the
clipper-side URL dedup index can be authoritatively refreshed:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/regen_vault_index.py" --vault <vault>
```

This overwrites `vault/_indexes/url-index.json` from a full vault scan,
correcting any drift accumulated since the last regen (incremental appends
from clipper PUTs and `/wiki-capture` are best-effort and may miss entries
across REST race windows). Single pass — the vault walk has already
happened in step 1. Commit the index alongside the triage file.

### 8. Report

Summarize to the user:
- Files skipped (count + reasons)
- Batches created (count + sizes + labels)
- Maybes deferred (count)
- Triage file written at `dev/ingest-triage-<date>.md`
- URL index regenerated (`vault/_indexes/url-index.json`, N entries)
- Suggested next step: "Pick up Batch 1 with `/wiki-ingest` — one article at a time."

## Rules

- **Never auto-ingest from triage.** Triage plans; `/wiki-ingest` executes.
  The two skills are complementary, not chained.
- **User approves every decision.** Present proposals; don't act silently.
- **Batch size is 5.** Matches the ingest cadence rule (overview refresh
  every 5 ingests). If the user wants larger batches, they can ask.
- **One triage file at a time.** If an active triage file exists with
  unchecked batches, prompt the user to continue or replace it.
- **Maybes are honest.** Don't force a decision on ambiguous files — defer
  them with a clear "decide at takeaway pass" trigger. The `/wiki-ingest`
  takeaway pass is the right place for borderline editorial judgment.
- **Per-article checkboxes are load-bearing.** Each batch article is a
  task-list item (`- [ ] **`<slug>.md`** …`), not a numbered list entry.
  `/wiki-ingest` ticks them on commit and `drill_state.py` parses the
  unticked ones to compute the "Batch X next / First up" pointer. The drill
  falls back to legacy numbered parsing for old files, but new triage files
  must use checkboxes so mid-batch progress survives across sessions.
