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

*Architecturally: Option C (inline tool-orchestration) per `_template-simple/SKILL.md`.
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

Capture `start_line` and `batch_id`; hold both for steps 4.6 and 4.7. (If the
probe errors, skip instrumentation silently — run the loop, write rows without
`--batch-id`, skip step 4.7. Cost telemetry is best-effort.)

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

#### 4.3 — Tier-classify the URLs

Mirror `/wiki-corroborate` § 4.3 verbatim — pipe the WebSearch URLs
through `classify_credibility` via inline Python heredoc:

```bash
WIKI_RC_URLS=$(cat <<'URLS_EOF'
<one URL per line, from this pair's WebSearch>
URLS_EOF
)
python - "$WIKI_RC_URLS" "<wiki>" <<'PY'
import json, sys
from pathlib import Path
urls_blob, wiki_root = sys.argv[1], sys.argv[2]
sys.path.insert(0, str(Path(wiki_root) / ".scripts"))
from extract_page_metadata import classify_credibility, _load_credibility_lists

tiers = _load_credibility_lists()
out = []
for url in urls_blob.splitlines():
    u = url.strip()
    if not u:
        continue
    out.append({"url": u, "tier": classify_credibility(u, tiers)})
print(json.dumps(out, ensure_ascii=False))
PY
```

Result: list of `{url, tier}` with tier ∈
`tier_1 | tier_2 | tier_3 | denylist | unclassified`.

#### 4.4 — Filter and annotate

Same surfacing rules as `/wiki-corroborate`:

- **Drop** anything classified `denylist`.
- **Drop** `tier_3` UNLESS `--include-tier-3` was passed.
- **Keep** `tier_1`, `tier_2`, `unclassified`.
- For `unclassified`: annotate `(unclassified — propose tier?)`.

If the filtered set is empty, the per-pair block's candidate list shows
`(no candidates above tier-3 — try --include-tier-3?)`. Still emit the
block; still write a telemetry row.

#### 4.5 — Format the per-pair block

```text
═══════════════════════════════════════════════
[<N>/<M>] <pair_a_path>
       ↕ contradicts ↕
       <pair_b_path>
       shape: <shape> | A: <last_updated_a> | B: <last_updated_b>
       ⚠ disagreement: <callout_excerpt wrapped at ~70 cols, or "no inline callout — structural edge only">

       [<tier>] <url>
                "<title>"
       ...

       ───────────────────────────────────────────
       Resolution outcomes (HITL — user decides at /wiki-ingest gate):
         [a_wins]     side A's claim stands; aggregator edited with
                      `> [!NOTE] Resolved: …` callout citing the new
                      source; contradicts: edge removed from both sides
         [b_wins]     mirror — side B's claim stands
         [both_stand] genuine disagreement preserved; contradicts: edge
                      downgraded to mutual `## Related` cross-link
                      annotated `(disagreement examined — both
                      positions stand)`
         [defer]      no decision today; edge stays

       Fallback rubric (Jadad four-criterion, when candidates exist
       for both sides):
         1. Relevance         — most directly addresses the disputed claim
         2. Recency           — methodology/data is more current
         3. Comprehensiveness — broader coverage of the disputed scope
         4. Low-bias          — cleaner authorship / credibility chain

       Default posture (Wikipedia NPOV): describe, don't resolve.
```

Tier annotations: `[tier-1]` / `[tier-2]` / `[tier-3]` (if shown) /
`[unclassified — propose tier?]`. URL on the header line, title on
the line below indented under the URL. Ordering within a block: tier_1
first, then tier_2, then tier_3 (if shown), then unclassified.

When a candidate URL matches a source already in either side's
`sources:` frontmatter, append `(already in wiki — cited by side A | B)`
to the title line. Cross-check by reading both pages' frontmatter once
at loop entry.

#### 4.5b — Coverage gate (mirrors `/wiki-corroborate` § 4.5b)

After emitting the per-pair block but BEFORE writing the telemetry row,
evaluate classifier coverage on the filtered candidate set:

```python
total = len(filtered_candidates)
unclassified = sum(1 for c in filtered_candidates if c["tier"] == "unclassified")
unclassified_ratio = unclassified / total if total else 0
```

If `unclassified_ratio > 0.60`, surface to the user:

```text
⚠ Classifier coverage gap: <unclassified> of <total> candidates are unclassified.
Propose tiers for the unclassified set now? Y/N
```

**Threshold rationale.** Inherited from `/wiki-corroborate` (tuned 2026-05-12
from 29-row dataset; see session 55 narrative). The threshold and
cascade-update flow are identical — see `wiki-corroborate/SKILL.md`
§ 4.5b for the full mechanics. On `Y`, prompt per unclassified domain,
collect `1 / 2 / 3 / denylist / skip`, write accepted entries to
`url_classifier_lists.json` in one atomic edit, commit with:

```text
telemetry: extend url_classifier_lists.json — <N> domains from <pair_key> coverage gate
```

On `N` or silence, proceed to § 4.6. Skip when `total == 0`.

#### 4.6 — Append a telemetry row

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_contradiction_log.py" --wiki <wiki> \
  --pair-key "<key>" \
  --pair-a-slug "<a>" --pair-a-type "<type-a>" \
  --pair-b-slug "<b>" --pair-b-type "<type-b>" \
  --shape "<shape>" \
  --callout-excerpt "<excerpt, JSON-quoted if needed>" \
  --candidates-returned <N filtered candidates> \
  --candidates-by-tier '{"tier_1":N,"tier_2":N,"tier_3":N,"unclassified":N}' \
  --candidate-urls '[{"url":"<url>","tier":"<tier>"}, ...]' \
  --include-tier-3-flag <true|false> \
  --batch-id "<batch_id from step 3.5>" --skill-version "resolve-contradictions-v2"
```

Pass `--batch-id` (from step 3.5) on every per-pair row so step 4.7 can
backfill the invocation cost onto them. Omit it only if step 3.5 was skipped.
`user_action` defaults to `deferred` and `outcome` defaults to `pending`
on the initial write. Both fields are backfilled later — `/wiki-capture`
flips `user_action` to `captured`; the ingestor sets `outcome` (one of
`a_wins | b_wins | both_stand | defer`) on the user's takeaway-gate reply.

Pass `--candidates-returned` as the count of items shown to the user
(post-filter), not the raw WebSearch count. `--candidate-urls` takes the
same `[{url, tier}, ...]` list as `/wiki-corroborate` — domains are
extracted via `_domain_from_url`; pass `'[]'` when WebSearch failed.

#### 4.7 — Close the cost-measurement window

Run **once**, after the per-pair loop (skip if step 3.5 was skipped). Probe the
window opened at 3.5, then stamp the invocation cost onto every row of this batch:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/session_cost_probe.py" --since-line <start_line>
```

Read `total_tokens`, `tool_uses`, `duration_ms`, `websearch_calls`,
`context_tokens_post_pass`, `context_tokens_pre_pass`, `context_growth`,
`output_tokens_pass` from the JSON, then:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_contradiction_log.py" --wiki <wiki> --backfill-cost \
  --batch-id "<batch_id>" \
  --total-tokens <total_tokens> --tool-uses <tool_uses> \
  --duration-ms <duration_ms> --websearch-calls <websearch_calls> \
  --context-tokens-post <context_tokens_post_pass> \
  --context-tokens-pre <context_tokens_pre_pass> \
  --context-growth <context_growth> \
  --output-tokens-pass <output_tokens_pass>
```

Same surface, batch-unit convention, and ROI rationale as `/wiki-corroborate`
§ 4.7 — the two skills' cost telemetry is pooled for the inline-vs-fork
decision. The backfill auto-stamps `flags_in_batch` (here = pairs in the batch);
the gate reads per-invocation `context_growth` for context discipline and
per-pair `total_tokens / flags_in_batch` for throughput, with `websearch_calls`
descriptive only. Pass `context_growth` as `null` if the probe found no
pre-window anchor.

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
  `/wiki-corroborate` § 4.5b — lists never grow silently.
- **Re-running on the same pair is fine.** No filter cache — newly-
  published adjudicating sources may have arrived. Cost is borne by the
  user via "skip this pair, next."
- **WebSearch failure → continue.** Emit a per-pair block with
  `(websearch failed: <error>)` in place of the candidate list. Still
  write the telemetry row with `--candidates-returned 0` for audit
  visibility. Don't abort the whole batch.
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
