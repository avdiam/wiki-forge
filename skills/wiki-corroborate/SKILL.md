---
name: wiki-corroborate
description: >-
  Search-and-surface skill for the `verify: true` review queue. For each flagged page, runs a tier-aware WebSearch for primary / authoritative corroborating sources and outputs a per-flag block of tier-classified candidate URLs. Resolution flows through normal `/wiki-capture` → `/wiki-ingest`; the verify-clearing decision happens at the ingestor's takeaway gate. Use when the user runs /wiki-corroborate, says "find sources for X", "corroborate this flag", or wants to drain the verify queue.
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

*Architecturally: inline tool-orchestration.
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
python "${CLAUDE_PLUGIN_ROOT}/scripts/list_verify_flags.py" --wiki <wiki> <FLAG_ARGS>
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
python "${CLAUDE_PLUGIN_ROOT}/scripts/session_cost_probe.py" --emit-marker
```

Capture `batch_id` from the JSON (it encodes `start_line`) and hold it for
steps 4.3 and 4.5. (If the probe errors — e.g. transcript not found in a
non-standard runtime — skip instrumentation silently: run the loop without
`--batch-id` and skip step 4.5. Cost telemetry is best-effort; never block a
drain on it.)

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

#### 4.3 — Classify, format and log (one call)

Pass this flag's WebSearch results on stdin, one per line as `<url> <title>`
(the URL has no spaces; the rest of the line is the title). A quoted heredoc
keeps `?`, `&` and `#` in URLs unexpanded:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/classify_candidates.py" <wiki> corroborate --slug <page_slug> --index <N>/<M> --batch-id "<batch_id>" <<'CANDIDATES'
<url> <title>
<url> <title>
CANDIDATES
```

Add `--include-tier-3` when the user passed it. Omit `--batch-id` only if
step 3.5 was skipped. With no results (WebSearch failed), run it with an
empty heredoc: a zero-candidate row is still written.

The script, so you don't do it by hand:

- **Reads the flag** (path, confidence, sources count, bucket, callout) from
  the same code as `list_verify_flags.py`.
- **Classifies** each URL against `url_classifier_lists.json`
  (`tier_1 | tier_2 | tier_3 | denylist | unclassified`), **drops**
  `denylist` always and `tier_3` unless `--include-tier-3`, and **orders**
  tier_1 → tier_2 → tier_3 → unclassified.
- **Marks** a candidate the page already cites (matched against the `url:` /
  `source_url:` / `source:` of the vault files in its `sources:`) with
  `(already in wiki — this is the source we're trying to corroborate)`.
- **Prints the per-flag block** (design § 4.6 format; an empty set shows
  `(no candidates above tier-3 — try --include-tier-3?)`), then a `Shown:`
  line with the per-tier counts.
- **Prints the coverage-gate prompt** when more than 60% of the shown
  candidates are unclassified (step 4.4).
- **Writes the telemetry row** to `corroborate_log.jsonl`: post-filter
  `candidates_returned`, `candidates_by_tier`, `candidate_domains` (host +
  first path segment, for seed-list backfill), `include_tier_3_flag`,
  `batch_id`, `skill_version: corroborate-v2`, `user_action: deferred`
  (backfilled later by `/wiki-capture` and the ingestor). The last line
  reads `Logged: 1 row …`; exit 3 with `Logged: ERROR …` means the block is
  fine but the row failed: tell the user and continue.

Relay the block to the user as printed. Exit 1 means bad input (the slug
isn't a `verify: true` page, or a line isn't `<url> <title>`): fix and rerun.

#### 4.4 — Coverage gate (v1.3)

When the output ends with `⚠ Classifier coverage gap: <u> of <t> candidates
are unclassified.` plus the domain list and `Propose tiers for the
unclassified set now? Y/N`, ask the user.

**Threshold rationale (tuned 2026-05-12 from a 29-row dataset; session 55
in `dev/sessions.md`).** The largest natural gap in per-row
`unclassified_ratio` lies between 0.50 and 0.62; the strict `> 0.60` sits
inside it, so it fires on the silting tail without firing on clean rows
where vendor docs were already classified. The script owns the math.

**On `Y` — batch cascade-update flow.** For each listed domain (path prefix
preserved, e.g. `github.com/anthropic`, `linkedin.com/in/<user>`):

1. Show the domain.
2. Prompt: `tier? (1 / 2 / 3 / denylist / skip)`.
3. Collect the user's choice verbatim; never assume.

Then write the accepted entries to the plugin's canonical
`${CLAUDE_PLUGIN_ROOT}/scripts/url_classifier_lists.json` (locate via Glob
`**/scripts/url_classifier_lists.json` for the Edit) in one atomic edit:
tier_1 / tier_2 / tier_3 into the matching seed list, `denylist` into the
denylist; `skip` is not persisted and re-surfaces next time. Commit with:

```text
telemetry: extend url_classifier_lists.json — <N> domains from <page_slug> coverage gate
```

**On `N`, silence or an ambiguous reply:** continue the loop. The per-line
`[unclassified — propose tier?]` annotations stay as the fallback; the gate
is a soft prompt, never a hard stop. The row (already written) doesn't
record the gate decision; a `coverage_gate: {fired, decision}` field is
filed as a possible v1.3 row extension.

#### 4.5 — Close the cost-measurement window

Run **once**, after the per-flag loop (skip if step 3.5 was skipped):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/classify_candidates.py" <wiki> close corroborate --batch-id "<batch_id>"
```

It probes the transcript window opened at step 3.5 (the batch id encodes
its start line) and stamps the invocation cost (`total_tokens`,
`tool_uses`, `duration_ms`, `websearch_calls`, `context_tokens_post/pre`,
`context_growth`, `output_tokens_pass`; null fields skipped) onto every row
of the batch, plus `flags_in_batch` (the per-flag denominator). A probe
failure prints a note and leaves cost null: best-effort, never block a
drain on it. The window includes this call's own turn — negligible and
constant across runs.

**Batch-unit convention (2026-06-08):** the fork-vs-inline gate reads
**per-invocation `context_growth`** for context discipline (the absolute
`context_tokens_post_pass` is contaminated by how full the session already
was — the session-95 warmup taint) and **per-flag `total_tokens /
flags_in_batch`** for throughput. `websearch_calls` (≈1/flag) is
descriptive, not a standalone gate.

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
  confirm the proposed tier; update the plugin's canonical `${CLAUDE_PLUGIN_ROOT}/scripts/url_classifier_lists.json`
  on the next operation. Lists never grow silently — every addition is
  user-confirmed. Same shape as session 47's `auth_required` cascade.
- **Re-running on the same flag is fine.** No filter cache — newly-published
  corroboration may have arrived. Cost is borne by the user via "skip this
  flag, next" rather than skill-side filtering.
- **WebSearch failure → continue.** If WebSearch fails (rate limit, network),
  run step 4.3 with an empty heredoc (the row logs zero candidates, for audit
  visibility) and add `(websearch failed: <error>)` under the block. Don't
  abort the whole batch.
