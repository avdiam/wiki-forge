---
name: wiki-resolve-contradictions
description: >-
  Search-and-surface skill for the typed `contradicts:` edge queue. For each unique pair, runs a tier-aware WebSearch for authoritative adjudicating sources and outputs a per-pair block of tier-classified candidate URLs alongside the three-outcome decision menu (A wins / B wins / both stand) and the Jadad four-criterion fallback rubric. Resolution flows through normal `/wiki-capture` → `/wiki-ingest`; the edge-removal + Resolved-callout decision happens at the ingestor's takeaway gate's new "Contradiction resolution" subsection. Sibling to `/wiki-corroborate`. Use when the user runs /wiki-resolve-contradictions, says "find adjudicating sources for X vs Y", "resolve this contradiction", or wants to drain the contradicts: queue.
argument-hint: "<pair-key> | --batch X-Y | --all [--include-tier-3]"
---

# wiki-resolve-contradictions

User-facing search-and-surface skill for the typed `contradicts:` edge
queue. The schema's symmetric `contradicts: [[other]]` frontmatter pairing
flags two pages that disagree on a claim; `lint_wiki.py`'s
`check_asymmetric_contradictions` keeps the edges symmetric. This skill
closes the friction-to-find-an-adjudicating-source loop: for each unique
pair it builds a single general-adjudication WebSearch prompt, classifies
the returned URLs against `url_classifier_lists.json`'s credibility tiers,
and prints a per-pair block with the three-outcome decision menu and the
Jadad four-criterion fallback rubric the user can apply.

**The skill never bypasses HITL.** It identifies, surfaces candidates, and
stops. The user picks a candidate (or skips). `/wiki-capture` writes the
vault file. `/wiki-ingest` chains in and surfaces the resolution gate at
the takeaway approval step. The `contradicts:` edge is removed only on
the user's verbatim outcome decision at that gate (or via manual edit —
wiki pages are editable per project discipline; vault is not).

*Architecturally: inline tool-orchestration.
Main-session only — no subagent dispatch, no librarian, no token cost beyond
this skill's prompt + WebSearch round-trips.*

**Sibling to `/wiki-corroborate`.** Shares the URL classifier
infrastructure, the coverage-gate cascade-update pattern, the
paged-batch output format, and the telemetry JSONL shape. Differs on
queue source (`contradicts:` edges vs `verify: true` flags), prompt
target (adjudication vs corroboration), and user-decision space
(three outcomes vs binary). When in doubt about a shape choice, mirror
`/wiki-corroborate`.

## Workflow

### 1. Locate the wiki

Find the wiki root (`wiki/CLAUDE.md`) in the current directory or upward.
If none, tell the user and suggest `/wiki-init`.

### 2. Parse arguments

The argument grammar is small but explicit — three input shapes plus one flag:

| Form | Meaning |
|---|---|
| `<pair-key>` | Single-pair mode. Key is `slug-a\|slug-b` (canonical form, slugs sorted ascending), or `slug-a:slug-b`, or `slug-a~slug-b` — either slug order. |
| `--batch X-Y` | 1-indexed inclusive range over the oldest-pair-first ordering. |
| `--all` | Every pair in the queue. |
| (none) | Default: `--batch 1-10` (oldest 10 pairs). |
| `--include-tier-3` | (modifier) Surface tier-3 hits alongside tier-1+tier-2. Default: tier-3 dropped. |

### 3. Enumerate pairs

Run `list_contradictions.py`; capture stdout as JSON:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/list_contradictions.py" --wiki <wiki> <PAIR_ARGS>
```

Where `<PAIR_ARGS>` is `--single <pair-key>` / `--batch X-Y` / `--all` /
nothing (default first-10). The script returns:

```json
{
  "total": 9,
  "returned": 9,
  "by_shape": {"source-source": 4, "concept-source": 2, ...},
  "pairs": [
    {"pair_key": "...", "pair_a_slug": "...", "pair_a_path": "...",
     "pair_a_type": "...", "pair_a_category": "...",
     "pair_a_last_updated": "...",
     "pair_b_slug": "...", "pair_b_path": "...", "pair_b_type": "...",
     "pair_b_category": "...", "pair_b_last_updated": "...",
     "shape": "...", "asymmetric": false, "callout_excerpt": "..."},
    ...
  ]
}
```

If `total == 0`, tell the user the queue is empty and stop.

If `returned == 0` and the user passed `--single <pair-key>` that doesn't
match any pair, tell the user the key doesn't match any current edge
(offer `--all` to see what is) and stop.

If any pair has `asymmetric: true`, surface a warning above the per-pair
output: `⚠ <N> asymmetric edge(s) present — fix via lint_wiki.py before
resolving.` The skill still emits blocks for those pairs (the queue
visibility matters more than the data-quality nag) but the user should
know.

### 3.5 — Open the cost-measurement window

Run **once**, immediately before the per-pair loop, only when `returned > 0`.
Captures the session-transcript line count so the invocation's main-session
cost can be reconstructed at close — the same fork-vs-inline ROI telemetry
`/wiki-corroborate` records (`dev/tech-debt.md` "execution shape" entry). This
skill is main-session only, so the harness emits no `<usage>` block; the
transcript is the cost surface, read after the fact.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/session_cost_probe.py" --emit-marker
```

Capture `batch_id` (it encodes `start_line`); hold it for steps 4.3 and 4.5.
(If the probe errors, skip instrumentation silently — run the loop without
`--batch-id`, skip step 4.5. Cost telemetry is best-effort.)

### 4. Per-pair loop

For each pair in `returned`:

#### 4.1 — Build the WebSearch prompt

**One general adjudication prompt** — no bucket variants. The pair-shape
heterogeneity (source↔source / source↔concept / concept↔concept /
mixed) means a single prompt is the honest start; expand to bucket
variants only if telemetry surfaces a per-shape quality gap.

Template (interpolate `<page-a>`, `<page-b>`, `<callout>`):

```
Find authoritative sources that adjudicate the disagreement between
<page-a-title> and <page-b-title>, or that explicitly address the
disputed claim from a position of authority.

Bias toward, in order: peer-reviewed papers, survey papers and
meta-analyses, vendor official documentation (when relevant),
authoritative standards bodies / RFCs, named-publication deep-dives
with explicit methodology. Avoid: re-publishers, content-farm
summaries, opinion pieces without primary-source grounding.

Specific disagreement: <callout_excerpt, if present — else: "the pages
disagree on a claim attested in their respective frontmatter contradicts:
edges; no inline callout is present">

Return 3-5 candidate links with titles and one-line descriptions. Do not
synthesize an adjudication verdict — the user evaluates and captures.
```

When `callout_excerpt` is `null` (typical for source↔source pairs),
substitute the second-best signal: `"both pages cover {pair_a_category}
and disagree on a claim in scope; the contradicts: edge in their
frontmatter is the only structural anchor"`. Source↔source pairs are
4/9 of the current corpus — this case is not exotic.

#### 4.2 — Invoke WebSearch

Make one WebSearch tool call per pair with the prompt from § 4.1.
Capture the returned URLs + titles + descriptions.

#### 4.3 — Classify, format and log (one call)

Same script as `/wiki-corroborate` § 4.3, `resolve` mode. Pass this pair's
WebSearch results on stdin, one per line as `<url> <title>`:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/classify_candidates.py" <wiki> resolve --pair "<pair_key>" --index <N>/<M> --batch-id "<batch_id>" <<'CANDIDATES'
<url> <title>
<url> <title>
CANDIDATES
```

Add `--include-tier-3` when the user passed it; omit `--batch-id` only if
step 3.5 was skipped; with no results, run it with an empty heredoc.

The script reads the pair (paths, shape, both `last_updated`, the callout
excerpt) from the same code as `list_contradictions.py`; classifies,
filters (`denylist` always dropped, `tier_3` unless `--include-tier-3`) and
orders the candidates exactly as corroborate does; marks a candidate either
side already cites with `(already in wiki — cited by side A | B)`; prints
the per-pair block below; prints the coverage-gate prompt above 60%
unclassified; and writes the `contradiction_log.jsonl` row (post-filter
counts, `candidate_domains`, `batch_id`,
`skill_version: resolve-contradictions-v2`; `user_action` starts
`deferred` and `outcome` `pending`, backfilled later by `/wiki-capture` and
the ingestor). Exit 3 with `Logged: ERROR …` = the row failed (tell the
user, continue); exit 1 = bad input (no such `contradicts:` pair).

Block shape (printed by the script; relay it as is):

```text
═══════════════════════════════════════════════
[<N>/<M>] <pair_a_path>
       ↕ contradicts ↕
       <pair_b_path>
       shape: <shape> | A: <last_updated_a> | B: <last_updated_b>
       ⚠ disagreement: <callout excerpt, or "no inline callout — structural edge only">

       [<tier>] <url>
                "<title>"
       ...

       Resolution outcomes (a_wins / b_wins / both_stand / defer),
       Jadad four-criterion fallback rubric, NPOV default posture
```

#### 4.4 — Coverage gate (mirrors `/wiki-corroborate` § 4.4)

When the output ends with the `⚠ Classifier coverage gap` prompt, ask the
user. Threshold and cascade-update flow are identical — see
`wiki-corroborate/SKILL.md` § 4.4. On `Y`, prompt per listed domain,
collect `1 / 2 / 3 / denylist / skip`, write accepted entries to
`url_classifier_lists.json` in one atomic edit, commit with:

```text
telemetry: extend url_classifier_lists.json — <N> domains from <pair_key> coverage gate
```

On `N` or silence, continue the loop.

#### 4.5 — Close the cost-measurement window

Run **once**, after the per-pair loop (skip if step 3.5 was skipped):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/classify_candidates.py" <wiki> close resolve --batch-id "<batch_id>"
```

Same surface, batch-unit convention, and ROI rationale as `/wiki-corroborate`
§ 4.5 — the two skills' cost telemetry is pooled for the inline-vs-fork
decision. `flags_in_batch` here = pairs in the batch; the gate reads
per-invocation `context_growth` for context discipline and per-pair
`total_tokens / flags_in_batch` for throughput, with `websearch_calls`
descriptive only.

### 5. Footer

After the last block:

```text
Showing <X>-<Y> of <total>. Re-run with `/wiki-resolve-contradictions --batch <next-range>` for the next page.
```

Show only when `returned < total`. On `--all`, no "next page" — final-batch
footer is `<total> pairs surveyed.`

Forward-insurance footer (always emitted, single lines):

```text
Composes with: per-claim *[AMBIGUOUS]* markers (future) — when the queue
shifts to claim-level granularity, the search target sharpens from pair
callout to specific claim text.
/wiki-ask classifier pattern: "resolve contradiction between X and Y" /
"adjudicate X vs Y".
```

### 6. Closing the loop (informational only)

The skill stops at surface. To resolve a pair:

1. User picks a candidate URL from a per-pair block.
2. `/wiki-capture <picked-url>` writes the vault file and chains into `/wiki-ingest`.
3. `/wiki-ingest` step 11 takeaway gate surfaces the new
   **Contradiction resolution** subsection (added in this ship — see
   `.claude/skills/wiki-ingest/SKILL.md`). The subsection surfaces every
   pair the ingest's EDIT targets participate in, with the same
   three-outcome menu the skill printed.
4. User picks `a_wins | b_wins | both_stand | defer`. The ingestor
   applies the corresponding edit:
   - **a_wins / b_wins** — add `> [!NOTE] Resolved: <claim> stands
     per <citation>` callout to the aggregator (the concept / entity /
     comparison side; or, for source↔source pairs, the concept page
     that cites both — caller picks). Remove `[[other-side]]` from both
     pages' `contradicts:` frontmatter.
   - **both_stand** — remove `[[other-side]]` from both pages'
     `contradicts:` frontmatter. Add mutual `## Related` entries with
     the annotation `(disagreement examined — both positions stand;
     see [[<citation>]])`. The schema's existing `## Related` section is
     the canonical home.
   - **defer** — no edits. Edge stays. Telemetry row's `outcome` set to
     `defer`; useful for tracking which pairs the user has actively
     deferred vs not-yet-seen.

5. The ingestor backfills the telemetry row via
   `append_contradiction_log.py --backfill-ingest --pair-key … --outcome …`.

**Source-side immutability for `a_wins/b_wins`.** When the "losing" side
of a pair is a source page, the Resolved callout lives on the
*aggregator* (the concept/entity that cites both sources), not on the
source. Vault rule: source pages are immutable in vault; their wiki-side
mirror pages under `pages/sources/` ARE editable but the convention is
to keep them as faithful summaries — corrections live on the
aggregators that synthesize across sources. The ingestor's contradiction-
resolution branch should default to editing the aggregator and explicitly
ask "which page should carry the Resolved callout?" only when no clear
aggregator exists.

**Manual-edit escape hatch.** For pairs where the user wants to resolve
without finding a new source (e.g., re-reading the existing sources
makes the call obvious), hand-edit the `contradicts:` frontmatter
directly. Wiki pages are editable. The corpus is small (9 pairs as of
shipping) — hand-edit is cheap. If telemetry surfaces friction, a
`--apply <pair> --as a_wins|b_wins|both_stand` flag can be added later;
not worth pre-emptive design.

## Rules

- **Read-only.** Never modify wiki pages. Never edit `contradicts:`
  frontmatter or remove the typed edges. The ingestor's takeaway gate
  (or manual edit) is the only path that closes a pair.
- **Main-session only.** No subagent dispatch.
- **No synthesis.** WebSearch returns candidates; classify; format.
  Never paraphrase the search results into a verdict — the three-
  outcome menu is decided by the user, not the skill.
- **No log.md entry.** `contradiction_log.jsonl` is the audit trail.
  Same convention as `/wiki-corroborate`, `/wiki-query`, `/wiki-graph`,
  `/wiki-summarize`.
- **Tier-3 default-suppressed.** `--include-tier-3` is the explicit
  user opt-in.
- **Cascade-update on user confirmation.** Identical to
  `/wiki-corroborate` § 4.4 — lists never grow silently.
- **Re-running on the same pair is fine.** No filter cache — newly-
  published adjudicating sources may have arrived. Cost is borne by the
  user via "skip this pair, next."
- **WebSearch failure → continue.** Run step 4.3 with an empty heredoc
  (the row logs zero candidates, for audit visibility) and add
  `(websearch failed: <error>)` under the block. Don't abort the whole
  batch.
- **`superseded_by:` is NOT used by this flow.** That field is reserved
  for its original entity-version use case (e.g., `gpt-3 →
  superseded_by: [[gpt-4]]`). The current corpus has zero such pairs;
  evidential disagreements use the `> [!NOTE] Resolved:` callout +
  edge-removal mechanic described in step 6 above.
- **No comparison-page promotion.** The `both_stand` outcome downgrades
  the edge to a mutual `## Related` cross-link. Promoting to a
  comparison page is a separate user-driven concern (run `/wiki-stub
  <a-vs-b>` manually if desired); the resolution skill doesn't auto-
  invoke it.
