---
name: wiki-crystallize
description: Extract the standalone atomic facts a filed synthesis page surfaced and merge them into the existing concept/entity pages that were missing them — turning a synthesis from a new leaf node into a write-side reinforcer of the prior graph. The 4th canonical wiki operation (Ingest → Query → Summarize → Crystallize → Lint). Use when the user runs /wiki-crystallize <synthesis-slug>, says "crystallize this synthesis", "extract the facts from <synthesis> into the concept pages", or wants a saved query answer to strengthen the rest of the wiki. Merge-only and human-gated; never auto-creates pages, never auto-fires.
argument-hint: "<synthesis-slug>"
---

# wiki-crystallize

Crystallization lifts the **atomic standalone facts** a synthesis page surfaced
and merges each into the **existing** concept/entity page that should own it,
with trace-determined provenance and a `{#claim-id}` anchor. A `/wiki-query`
synthesis is a new leaf node; crystallization makes it a *write-side reinforcer*
of the prior knowledge graph (Ghumare's framing — [[rohit-ghumare]] Stream 10).

*Architecturally: (inline orchestration — the model lifts + routes the
facts, `crystallize.py` mechanizes provenance/dedup/anchors, the main session
applies the approved merges inline per Path 1 of the subagent-HITL doc). v1 of
Phase-2 build-set stream #2; design `dev/2026-06-16-crystallization-v1-design.md`.*

**v1 scope (hard boundaries):**
- **Manual trigger only.** No auto-firing on query completion — that collides with
  the non-negotiable HITL takeaway gate, and the prior art ([[2026-03-26-claude-code-dream-auto-dream-never-lose-memory]]
  Auto-Dream) ships auto-firing feature-flagged, not default. Deferred.
- **Merge-only.** Facts merge into EXISTING pages; a homeless fact is *reported*,
  never auto-created (honors the >3-sentence substantial-coverage threshold and the
  no-thin-stubs rule).
- **Human-gated fan-out.** Nothing is written until the user approves the full
  fact→page table — the backfire-mode guard against a bad synthesis fanning out
  across pages and needing a multi-page rollback.

## Workflow

### 1. Locate the wiki, vault, and synthesis page
Find the wiki root (look for `wiki/CLAUDE.md` in the current dir or upward); the raw
vault is its sibling `vault/`. The synthesis page is `wiki/pages/syntheses/<slug>.md`.
If it doesn't exist, list the available synthesis slugs (`pages/syntheses/*.md`) and ask.
Accept any filed synthesis — including `/wiki-summarize` coverage reports (`subtype:
coverage-report`), though those are usually thin on novel atomic facts.

### 2. Read the synthesis page in full
You need its full body (the narrative facts) and its `sources:` frontmatter (the
evidence base INFERRED facts inherit).

### 3. Extract candidate atomic facts (your judgment — not scriptable)
Lift **atomic, standalone, declarative** factual claims from the narrative — the
kind that belong as a bullet on a concept/entity page, NOT narrative connective
tissue, questions, recommendations, or meta-commentary about the synthesis itself.

- **Bias hard toward facts MISSING from existing pages.** A synthesis is built from
  pages that already exist; most of its prose is already on those pages. Crystallization
  is about the *new* atomic facts the synthesis surfaced or sharpened — not re-merging
  what it drew from. The script's dedup pass is a backstop, not a substitute for this judgment.
- One fact per claim. Keep each as a single clean sentence (it becomes a claim bullet verbatim).
- Aim for a handful of high-value facts, not an exhaustive shred. Fewer good facts beat many weak ones.

### 4. Route each fact to an existing home (merge-only)
For each candidate, find the best existing concept/entity page using the established
discovery machinery — do **not** guess slugs:
```bash
grep -ril "<key term>" wiki/pages/concepts wiki/pages/entities wiki/pages/comparisons
PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/hybrid_search.py" wiki "<fact>" --top 5
```
Assign each fact a `target` slug, or `null` if no existing page is a genuine home
(merge-only: homeless facts are reported, never created). Don't route a fact to a
page it doesn't actually belong on just to avoid `null` — a precise `unhomed` report
is more useful than a bad merge.

### 5. Build the mechanical merge plan
Write the routed facts to a temp file as a JSON list of `{"fact": "...", "target": "slug-or-null"}`
and run the planner (creates `.claude/tmp/` if absent):
```bash
mkdir -p .claude/tmp
cat > .claude/tmp/crystallize-facts.json <<'JSON'
[ {"fact": "...", "target": "..."}, ... ]
JSON
python "${CLAUDE_PLUGIN_ROOT}/scripts/crystallize.py" wiki <synthesis-slug> \
  --facts-json .claude/tmp/crystallize-facts.json --vault vault --json
```
The planner returns, per fact, a `status` (`merge` / `already_present` / `unhomed`)
and for merges: the trace-determined `tag` (""=EXTRACTED, or `*[INFERRED]*`),
`provenance` (`verbatim`/`synthesized`), pinned `sources`, `sources_to_add`, a unique
`claim_id`, and the ready-to-insert `claim_line`. It does NOT write anything.

### 6. Present the fan-out approval gate (HITL — non-skippable)
Show the plan as a table the user can audit before any write:

```
Crystallizing [[<synthesis-slug>]] → N facts

| # | Fact | → Page | Tag | Source(s) | claim-id |
|---|------|--------|-----|-----------|----------|
| 1 | <fact> | [[target]] | EXTRACTED | [[src]] | claim-… |
| 2 | <fact> | [[target]] | INFERRED  | synth underlying | claim-… |

Already on target page (skipped): <fact> → [[page]]
Unhomed (no existing page — not merged): <fact>

Approve all / edit (drop #, retarget #, reword #) / cancel?
```
Wait for the user's reply. Apply edits (drop, retarget → re-run step 5 for changed
rows, reword) before writing. **Never write before approval** — this gate is the
whole backfire-mode protection.

### 7. Apply the approved merges inline
For each approved `merge` row, edit its target page:
- Insert `claim_line` as a bullet under the most appropriate existing section
  (entity → `## Key attributes`; concept → `## Evidence` or the most relevant
  section; create the section only if none fits). Place it where it reads naturally.
- Add each path in `sources_to_add` to the page's `sources:` frontmatter.
- Bump the page's `last_updated:` to today; nudge `confidence:` upward only if a new
  corroborating source was added (same discipline as ingest edits).
- For an EXTRACTED fact, optionally attach the verbatim source quote as an inline
  `> "…" — [[source]]` blockquote under the bullet — this gives the A3 cascade an
  inline-attribution pin (most precise) instead of relying on the verbatim-trace tier.
  Worth it for claims likely to be contested; skip for routine facts.

Group edits per page (one Edit pass per target). Do not touch the synthesis page
itself or any vault source.

### 8. Regenerate index, commit, lint, log
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/update_index.py" wiki
git add -A && git commit -m "crystallize: <synthesis-slug> → <N> facts into <M> pages"
python "${CLAUDE_PLUGIN_ROOT}/scripts/lint_wiki.py" wiki --json   # mechanical post-op lint
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" crystallize \
  --title "<synthesis-slug> → <N> facts" \
  --updated "<comma-sep target page paths>" \
  --commit "<hash>" --wiki wiki
```
Surface any new lint findings the merges introduced (e.g. a fresh `claim_id_issues`
or `excessive_ambiguous` flag) in the closing report; fix or note them.
Delete the temp file: `rm -f .claude/tmp/crystallize-facts.json`.

### 9. Report
Summarize: facts merged (with target pages + tags), facts skipped as already-present,
facts left unhomed (the user's queue to decide on later), and the commit hash. If any
fact was left unhomed and clearly deserves a page, suggest `/wiki-stub` or a future ingest.

## Rules
- **Merge-only.** Never auto-create a page in v1. Unhomed facts are reported, full stop.
- **Human-gated.** The step-6 fan-out table is non-skippable. No writes before approval.
- **Provenance is trace-determined, never guessed.** The planner pins EXTRACTED facts to
  a specific verbatim source (∩ the synthesis's own sources — the S5 guard) and tags the
  rest `*[INFERRED]*` against the synthesis's underlying sources. Don't override its tagging
  by hand without a reason.
- **Don't re-merge what the synthesis drew from.** Extraction (step 3) targets *novel*
  atomic facts; the dedup pass is a backstop, not the filter.
- **Untagged = EXTRACTED.** A verbatim-traced fact carries no marker, only its `{#claim-id}`
  (the implicit-EXTRACTED convention). Only synthesized facts get `*[INFERRED]*`.
- **One commit for the operation**, message `crystallize: <slug> → N facts into M pages`.
- **Vault is immutable** and the **synthesis page is the origin** — neither is edited here.
- **Telemetry deferred (v1).** A `crystallize` entry lands in `log.md`; no `crystallize_log.jsonl`
  sidecar until auto-firing/cadence questions arise (design doc § Deferred).
