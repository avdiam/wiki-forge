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

#### 4.6 — Append a telemetry row

```bash
python <wiki>/.scripts/append_corroborate_log.py --wiki <wiki> \
  --page-slug "<slug>" --page-type "<type>" --page-bucket "<A|B|C>" \
  --callout-text "<callout, JSON-quoted if needed>" \
  --candidates-returned <N filtered candidates> \
  --candidates-by-tier '{"tier_1":N,"tier_2":N,"tier_3":N,"unclassified":N}' \
  --candidate-urls '[{"url":"<url>","tier":"<tier>"}, ...]' \
  --include-tier-3-flag <true|false> \
  --user-action deferred
```

`--user-action deferred` is the initial state — backfilled later by `/wiki-capture`
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
