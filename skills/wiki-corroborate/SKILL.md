---
name: wiki-corroborate
description: Search-and-surface skill for the `verify: true` review queue. For each flagged page, runs a tier-aware WebSearch for primary / authoritative corroborating sources and outputs a per-flag block of tier-classified candidate URLs. Resolution flows through normal `/wiki-capture` → `/wiki-ingest`; the verify-clearing decision happens at the ingestor's takeaway gate. Use when the user runs /wiki-corroborate, says "find sources for X", "corroborate this flag", or wants to drain the verify queue.
argument-hint: "<slug> | --batch X-Y | --all [--include-tier-3]"
---

# wiki-corroborate

User-facing search-and-surface skill for the `verify: true` review queue. The
schema flags single-source / unsourced claims with `verify: true` + an inline
`⚠ needs verification: <why>` callout; lint surfaces the queue (`needs_verification`
counter). This skill closes the friction-to-find-a-corroborating-source loop:
for each flagged page it builds a WebSearch prompt biased to the page's
remediation bucket and tier preferences, classifies the returned URLs against
`url_classifier_lists.json`'s credibility tiers, and prints a per-flag block of
candidates the user can capture via `/wiki-capture`.

**The skill never bypasses HITL.** It identifies, surfaces candidates, and
stops. The user picks a candidate (or skips). `/wiki-capture` writes the vault
file. `/wiki-ingest` chains in and surfaces the verify-clearing gate at the
takeaway approval step. The flag clears only on user Y at that gate.

*Architecturally: Option C (inline tool-orchestration) per `_template-simple/SKILL.md`.
Main-session only — no subagent dispatch, no librarian, no token cost beyond
this skill's prompt + WebSearch round-trips.*

## Workflow

### 1. Locate the wiki

Find the wiki root (`wiki/CLAUDE.md`) in the current directory or upward. If
none, tell the user and suggest `/wiki-init`.

### 2. Parse arguments

The argument grammar is small but explicit — three input shapes plus one flag:

| Form | Meaning |
|---|---|
| `<slug>` | Single-page mode. Slug is bare (`kimi-k2-6`), `[[wikilink]]`, or `<slug>.md`. |
| `--batch X-Y` | 1-indexed inclusive range over the oldest-flagged-first ordering. |
| `--all` | Every flag in the queue. |
| (none) | Default: `--batch 1-10` (oldest 10 flags). |
| `--include-tier-3` | (modifier) Surface tier-3 hits alongside tier-1+tier-2. Default: tier-3 dropped. |

### 3. Enumerate flags

Run `list_verify_flags.py`; capture stdout as JSON:

```bash
python <wiki>/.scripts/list_verify_flags.py --wiki <wiki> <FLAG_ARGS>
```

Where `<FLAG_ARGS>` is `--single <slug>` / `--batch X-Y` / `--all` / nothing
(default first-10). The script returns:

```json
{
  "total": 42,
  "returned": 10,
  "buckets_total": {"A": 17, "B": 20, "C": 5},
  "flags": [
    {"page_slug": "...", "page_path": "wiki/...", "page_type": "...",
     "page_bucket": "A|B|C", "callout_text": "...", "sources_count": N,
     "confidence": 0.7, "last_updated": "YYYY-MM-DD"},
    ...
  ]
}
```

If `total == 0`, tell the user the queue is empty and stop.

If `returned == 0` and the user passed `--single <slug>` that doesn't match
any flagged page, tell the user the slug isn't currently flagged (offer
`--all` to see what is) and stop.

### 3.5 — Open the cost-measurement window

Run **once**, immediately before the per-flag loop, only when `returned > 0`
(there are flags to search). This captures the session-transcript line count so
the invocation's main-session cost can be reconstructed at close — the telemetry
that answers the inline-vs-subagent ROI question (`dev/tech-debt.md` "execution
shape" entry). `/wiki-corroborate` is main-session only, so the harness emits no
`<usage>` block; the transcript is the only cost surface, read after the fact.

```bash
python <wiki>/.scripts/session_cost_probe.py --emit-marker
```

Capture `start_line` and `batch_id` from the JSON. Hold both for steps 4.6 and
4.7. (If the probe errors — e.g. transcript not found in a non-standard runtime
— skip instrumentation silently: run the loop, write rows without `--batch-id`,
and skip step 4.7. Cost telemetry is best-effort; never block a drain on it.)

### 4. Per-flag loop

For each flag in `returned`:

#### 4.1 — Build the WebSearch prompt

Three bucket-specific prompt shapes. Pick by `page_bucket`:

**Bucket A — entity bio thinness (`category: ai-practitioners`):**

```
Find authoritative biographical sources for <page-title>. Bias toward, in order:
academic profile pages, personal websites, fully-cited Wikipedia articles, named
publication profile articles, LinkedIn profile pages (linkedin.com/in/ only — not
posts/pulse). Avoid: forums, anonymous blogs, listicle aggregators, Medium
marketing posts.

Specific verification target: <callout_text>

Return 3-5 candidate links with titles and one-line descriptions. Do not
synthesize an answer — the user will evaluate and capture.
```

**Bucket B — entity with specific factual claims:**

```
Find primary-source corroboration for the following claim about <page-title>.
Bias toward, in order: vendor official documentation, vendor GitHub repos,
release notes / changelogs, named-publication technical reviews, peer-reviewed
papers when applicable. Avoid: re-publishers, content-farm summaries, vague
secondary commentary.

Specific verification target: <callout_text>

Return 3-5 candidate links with titles and one-line descriptions. Do not
synthesize an answer — the user will evaluate and capture.
```

**Bucket C — concept / comparison / synthesis:**

```
Find primary-source corroboration for the following claim from a concept page.
Bias toward, in order: peer-reviewed papers (arXiv / OpenReview / conference
proceedings), vendor whitepapers, RFCs / IETF drafts / standards bodies,
deep-dive articles in named publications. Avoid: thin secondary summaries.

Specific verification target: <callout_text>

Return 3-5 candidate links with titles and one-line descriptions. Do not
synthesize an answer — the user will evaluate and capture.
```

Same discipline as `/wiki-query`'s WebSearch onramp — primary / authoritative
sources, no synthesis, 3–5 candidates.

#### 4.2 — Invoke WebSearch

Make one WebSearch tool call per flag with the prompt from § 4.1. Capture
the returned URLs + titles + descriptions.

#### 4.3 — Tier-classify the URLs

Pipe the WebSearch URLs through `classify_credibility` to assign a tier per URL.
Inline Python heredoc (mirrors `/wiki-summarize`'s topic-passing convention —
URLs go through argv as a newline-joined blob; the heredoc is opaque to bash
so URLs survive `?`, `&`, `#`, etc. unescaped):

```bash
WIKI_CORROBORATE_URLS=$(cat <<'URLS_EOF'
<one URL per line, from this flag's WebSearch>
URLS_EOF
)
python - "$WIKI_CORROBORATE_URLS" "<wiki>" <<'PY'
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

The result is a list of `{url, tier}` objects with tier ∈
`tier_1 | tier_2 | tier_3 | denylist | unclassified`.

#### 4.4 — Filter and annotate

Apply the surfacing rules:

- **Drop** anything classified `denylist`.
- **Drop** `tier_3` UNLESS `--include-tier-3` was passed.
- **Keep** `tier_1`, `tier_2`, `unclassified`.
- For `unclassified`: annotate `(unclassified — propose tier?)` so the user
  can confirm a list addition.

If the filtered set is empty (every result was dropped), the per-flag block's
candidate list shows `(no candidates above tier-3 — try --include-tier-3?)`.
Still emit the block; still write a telemetry row.

#### 4.5 — Format the per-flag block

Per design § 4.6:

```text
═══════════════════════════════════════════════
[<N>/<M>] <page_path>
       confidence: <conf> | sources: <count> | bucket: <A|B|C>
       ⚠ needs verification: <callout_text wrapped at ~70 cols>

       [<tier>] <url>
                "<title>"
       ...
```

Tier annotations are bracketed by tier name: `[tier-1]` / `[tier-2]` /
`[tier-3]` (if shown) / `[unclassified — propose tier?]`. URL on the
header line, title on the line below indented under the URL.

Ordering within a block: tier_1 first, then tier_2, then tier_3 (if
shown), then unclassified.

When a candidate URL matches a source already in the page's `sources:`
frontmatter, append `(already in wiki — this is the source we're trying
to corroborate)` to the title line. Cross-check by reading the flag's
page frontmatter once at loop entry.

#### 4.5b — Coverage gate (v1.3)

After emitting the per-flag block in step 4.5 but BEFORE writing the
telemetry row in step 4.6, evaluate classifier coverage on the filtered
candidate set:

```python
total = len(filtered_candidates)  # post-step-4.4 list
unclassified = sum(1 for c in filtered_candidates if c["tier"] == "unclassified")
unclassified_ratio = unclassified / total if total else 0
```

If `unclassified_ratio > 0.60`, surface to the user:

```text
⚠ Classifier coverage gap: <unclassified> of <total> candidates are
unclassified.
Propose tiers for the unclassified set now? Y/N
```

**Threshold rationale (tuned 2026-05-12 from 29-row dataset; see session
55 narrative in `dev/sessions.md`).** The largest natural gap in observed
per-row `unclassified_ratio` lies between 0.50 and 0.62 (gap = 0.12; all
other adjacent-value gaps ≤ 0.10). A threshold of `> 0.60` sits robustly
inside that gap — fires on the silting tail (17/29 rows in tuning data,
including the 2026-05-08 batch with 0.597 aggregate silting rate) without
firing on clean rows where vendor docs were already classified (≤ 0.50,
11/29 rows). The threshold is the strict-greater form so a `0.60` row
doesn't trip the gate; the gap above 0.60 ensures the next data point is
already 0.62+.

**On `Y` — enter batch cascade-update flow.** For each unclassified
domain in the filtered set:

1. Show the domain (with path prefix preserved per step 4.3 — e.g.,
   `github.com/anthropic`, `linkedin.com/in/<user>`).
2. Prompt: `tier? (1 / 2 / 3 / denylist / skip)`.
3. Collect the user's choice verbatim; never assume.

After all domains have been classified, write the accepted entries to
`wiki/.scripts/url_classifier_lists.json` in one atomic edit (tier_1 /
tier_2 / tier_3 entries go into the corresponding seed list; `denylist`
entries go into the denylist; `skip` entries are NOT persisted — they
re-surface on the next `/wiki-corroborate` invocation that retrieves
them). Commit with message:

```text
telemetry: extend url_classifier_lists.json — <N> domains from <page_slug> coverage gate
```

Then proceed to step 4.6.

**On `N`** — proceed directly to step 4.6. The per-line
`[unclassified — propose tier?]` annotations from step 4.5 still appear
inline (the gate is **additive**, not a replacement); the user can
classify ad-hoc on the next operation. The gate's job is to surface the
option in batch — silence on `N` is a valid choice.

**On silence or ambiguous reply** — treat as `N` (proceed to 4.6). Do
not re-ask; the gate is a soft prompt, not a hard stop. The user can
re-run `/wiki-corroborate <slug>` for another chance, and the per-line
annotations remain as a fallback in the meantime.

**Skip when `total == 0`** — WebSearch failed or every candidate was
dropped by step 4.4's filter. The gate's coverage math is undefined for
an empty filtered set; jump directly to step 4.6 and write the row with
the v1.2 fields as today.

**Telemetry note (forward-insurance).** The current row schema (v1.2)
does not record gate fire / decision. Consider adding
`coverage_gate: {fired: <bool>, decision: "Y" | "N" | null}` as a v1.3
row-schema extension so future analysis can measure cascade-update
effectiveness. Filed as Phase C exit follow-on; not blocking for the
gate ship.

#### 4.6 — Append a telemetry row

```bash
python <wiki>/.scripts/append_corroborate_log.py --wiki <wiki> \
  --page-slug "<slug>" --page-type "<type>" --page-bucket "<A|B|C>" \
  --callout-text "<callout, JSON-quoted if needed>" \
  --candidates-returned <N filtered candidates> \
  --candidates-by-tier '{"tier_1":N,"tier_2":N,"tier_3":N,"unclassified":N}' \
  --candidate-urls '[{"url":"<url>","tier":"<tier>"}, ...]' \
  --include-tier-3-flag <true|false> \
  --batch-id "<batch_id from step 3.5>" --skill-version "corroborate-v2" \
  --user-action deferred
```

Pass `--batch-id` (from step 3.5) on every per-flag row so the invocation's
rows share an aggregation key; step 4.7 backfills the cost onto them. Omit
`--batch-id` only if step 3.5 was skipped. `--user-action deferred` is the
initial state — backfilled later by `/wiki-capture`
(to `captured`) and the ingestor (sets `flag_cleared_at_ingest` on Y). Pass
`--candidates-returned` as the count of items shown to the user (post-filter),
not the raw WebSearch count.

**`--candidate-urls`** takes the same post-filter `[{url, tier}, ...]` list
produced in step 4.3 (after the step 4.4 filter — i.e. denylist dropped,
`tier_3` dropped unless `--include-tier-3` was passed). The script extracts
each URL's host (with the first path segment preserved when present, so
`github.com/anthropic` and `linkedin.com/in/<user>` retain their
tier-distinguishing prefix) and stores them as
`candidate_domains: [{domain, tier}, ...]` on the row. Pass `'[]'` when
WebSearch failed or every candidate was dropped — the column distinguishes
"saw zero" (empty list) from "field absent on pre-v1.2 rows" (null). The
field's purpose is **seed-list backfill**: analysis of unclassified
domains across many rows surfaces real-world host patterns the
`url_classifier_lists.json` seeds miss, so the user can extend the lists
at decision-gate time per the cascade-update rule.

#### 4.7 — Close the cost-measurement window

Run **once**, after the per-flag loop finishes (skip if step 3.5 was skipped).
Probe the transcript window opened at step 3.5, then stamp the invocation-level
cost onto every row of this batch:

```bash
python <wiki>/.scripts/session_cost_probe.py --since-line <start_line>
```

Read `total_tokens`, `tool_uses`, `duration_ms`, `websearch_calls`,
`context_tokens_post_pass`, `context_tokens_pre_pass`, `context_growth`,
`output_tokens_pass` from the JSON, then:

```bash
python <wiki>/.scripts/append_corroborate_log.py --wiki <wiki> --backfill-cost \
  --batch-id "<batch_id>" \
  --total-tokens <total_tokens> --tool-uses <tool_uses> \
  --duration-ms <duration_ms> --websearch-calls <websearch_calls> \
  --context-tokens-post <context_tokens_post_pass> \
  --context-tokens-pre <context_tokens_pre_pass> \
  --context-growth <context_growth> \
  --output-tokens-pass <output_tokens_pass>
```

The window includes this step's own probe/backfill turns — negligible and
constant across runs. The backfill also auto-stamps `flags_in_batch` (= the
number of rows sharing this `batch_id`) — the per-flag normalization
denominator. **Batch-unit convention (2026-06-08):** the fork-vs-inline gate
reads **per-invocation `context_growth`** for context discipline (the absolute
`context_tokens_post_pass` is contaminated by how full the session already was —
the session-95 warmup taint) and **per-flag `total_tokens / flags_in_batch`**
for throughput. `websearch_calls` (≈1/flag) is descriptive, not a standalone
gate. If `context_growth` is `null` (probe found no pre-window anchor — e.g. the
pass began on transcript line 0), pass it as `null`; the backfill skips null
fields.

### 5. Footer

After the last block:

```text
Showing <X>-<Y> of <total>. Re-run with `/wiki-corroborate --batch <next-range>` for the next page.
```

Show only when `returned < total`. If the user passed `--all`, no
"next page" — final-batch footer is `<total> flags surveyed.`

Forward-insurance footer (always emitted, single lines):

```text
Composes with: per-claim *[AMBIGUOUS]* markers (future) — when the queue
shifts to claim-level granularity, the search target sharpens from page
callout to specific claim text.
/wiki-ask classifier pattern: "find sources for X" / "corroborate X".
```

### 6. Closing the loop (informational only)

The skill stops at surface. To resolve a flag:

1. User picks a candidate URL from a per-flag block.
2. `/wiki-capture <picked-url>` writes the vault file and chains into `/wiki-ingest`.
3. `/wiki-ingest` step 11 takeaway gate surfaces the **verify: true re-evaluation**
   subsection (added in this ship — see `.claude/skills/wiki-ingest/SKILL.md`).
4. User chooses Y (clear) / N (retain) / P (partial-narrow). The ingestor
   removes / retains / narrows the callout accordingly and backfills the
   telemetry row's `flag_cleared_at_ingest` on Y.

The skill itself does not edit pages — never. The verify-clearing decision is
HITL-mandatory and lives at the ingestor's gate.

## Rules

- **Read-only.** Never modify wiki pages. Never edit `verify: true` frontmatter
  or remove the inline callout. The ingestor's takeaway gate is the only path
  that clears flags.
- **Main-session only.** No subagent dispatch — the skill is a search +
  classify + format orchestration that's cheap to run in-session.
- **No synthesis.** WebSearch returns candidates; classify; format. Never
  paraphrase the search results into a verdict. The user evaluates and captures.
- **No log.md entry.** `corroborate_log.jsonl` is the audit trail for this
  read-and-surface operation. Same convention as `/wiki-query`, `/wiki-graph`,
  `/wiki-summarize`.
- **Tier-3 default-suppressed.** Common case is niche tools / recent releases /
  pseudonymous Medium authors where primary/secondary coverage is genuinely
  thin — `--include-tier-3` is the explicit user opt-in.
- **Cascade-update on user confirmation.** When an `unclassified` candidate is
  the right one (vendor docs we don't have on the list yet), the user can
  confirm the proposed tier; update `wiki/.scripts/url_classifier_lists.json`
  on the next operation. Lists never grow silently — every addition is
  user-confirmed. Same shape as session 47's `auth_required` cascade.
- **Re-running on the same flag is fine.** No filter cache — newly-published
  corroboration may have arrived. Cost is borne by the user via "skip this
  flag, next" rather than skill-side filtering.
- **WebSearch failure → continue.** If WebSearch fails (rate limit, network),
  emit a per-flag block with `(websearch failed: <error>)` in place of the
  candidate list. Still write the telemetry row with `--candidates-returned 0`
  for audit visibility. Don't abort the whole batch.
