---
name: wiki-ingest
description: Compile a vault source file into wiki pages. Owns the entire pre-write pipeline — pre-process + dedup probe + pre-screen + source read + discovery (grep + hybrid_search) + analysis + contradiction probe + takeaway approval gate — then dispatches the wiki-ingestor agent with a complete approved plan to execute the writes. The agent is invoked exclusively by this skill; capture chains here, never to the agent. Use when the user runs /wiki-ingest, says "ingest this file", "process this source", or after wiki-capture chains into it.
argument-hint: "<vault-path-or-all-new>"
---

# wiki-ingest (orchestrator + analyzer + approval gate)

*Architecturally: Option B (inline orchestration with Agent-tool dispatch at step 12) per `_template-complex/SKILL.md`.*

This skill is the **single entry point** to the wiki-ingestor agent. It
owns everything that happens before the writes: pre-flight scripts,
discovery, analysis, contradiction probe, and the takeaway-and-approval
gate. The agent receives a complete plan and executes — no discovery, no
analysis, no probe, no user interaction.

The approval gate stays in the main session because it accepts free-form
redirect input ("proceed with emphasis on X" / "skip the Y section" /
"redo — you misread Z"). Foreground subagents *can* fire structured
`AskUserQuestion` calls, but multi-choice doesn't capture conversational
free-form replies cleanly. The main thread is the right place for it.

## Workflow (14 steps)

### 1. Validate input

Expect one of:

- A path to a raw file: `/wiki-ingest vault/Web Articles/2026-04-15-foo.md`
- `all new` — scan `vault/` for unprocessed files via `lint_wiki.py`'s `source_backlog`
- `rerun <path>` — re-ingest a previously processed file

If no argument given: ask the user which file, or offer to scan `vault/`
(run `python wiki/.scripts/lint_wiki.py wiki --json` and read
`source_backlog`).

> [!NOTE]
> If `all new` finds more than 10 unprocessed files AND the wiki has fewer
> than 20 existing pages, warn: "Your wiki is still young. Consider
> ingesting 5–10 sources first to establish the taxonomy, then batch the
> rest. Large initial batches produce shallow cross-references because
> there are no existing pages to connect to."

### 2. Locate the wiki

Find `wiki/CLAUDE.md` in the current directory or upward. If none found,
tell the user, suggest `/wiki-init`, stop.

### 3. Pre-process the source

#### 3a. Filename normalization gate (Layer 2, defensive)

Before invoking `ingest_source.py`, verify the raw source path is canonical
(`^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$`). Bulk Chrome-extension drops, manual
drops, and any future capture path that bypasses `/wiki-capture` and the
clipper can carry filenames with apostrophes, spaces, em-dashes, or
non-ASCII. The wiki-side gate ensures the rest of the pipeline operates on
a canonical name; the standalone `wiki/.scripts/normalize_vault.py` is the
bulk pre-flight that handles the same case in batch.

If the path is non-canonical, run the normalizer scoped to the single file's
parent content directory (cheap — content directories are small, and this
keeps the rename blast radius to one folder rather than the whole vault):

```bash
python <wiki>/.scripts/normalize_vault.py <vault>/<content-dir>   # e.g. vault/Clippings
```

`normalize_vault.py` accepts either the vault root (walks every content
dir) or a single content dir directly (e.g. `vault/Clippings`); pass the
file's parent dir here. (Pointing it at a `.`/`_`-prefixed dir is a no-op
by design — skip stubs and protocol files are never normalized.)

The script halts on slug collisions; if it does, surface the message to the
user and stop the ingest — collision resolution is a manual call, not
automatic. On success, re-derive `<raw-file>` to the new canonical name
before continuing to `ingest_source.py`. Mention the rename in the takeaway
block at step 11 (`renamed: <original> → <canonical>`) so the user sees it
in the approval gate; the commit message in the agent's step 6 records it
implicitly via `git add ../<vault_source>` (the new name is already what
git tracks).

If the path is already canonical, this step is a no-op.

#### 3b. Pre-process

```bash
python <wiki>/.scripts/ingest_source.py --wiki <wiki> --source <raw-file> --json
```

Keep the returned JSON brief — it carries `title_guess`, `word_count`,
`sha256_short`, `existing_summary` / `merge_mode`, `suggested_slug`, and a
1200-char preview. You'll pass it to the agent in step 12 verbatim.

If `merge_mode: true`, this source was previously ingested. Tell the user
the existing summary path and ask whether to proceed (which will trigger
the agent's APPEND-`## Re-ingest <date>` mode) or skip. On skip, log a
`note` entry via `append_log.py` and exit.

### 4. Content-hash dedup probe

Before touching anything else, check whether another vault file produced a
byte-identical source page:

```bash
grep -l "^source_hash: <sha256_short>" <wiki>/pages/sources/*.md
```

If a match is found, present:

```text
Possible duplicate: <raw-file> has the same content hash (<sha256_short>) as
<matched-source-page>. The filename / URL / date may differ but the bytes
are identical.

Options:
  1. Skip — don't re-ingest; record the vault file as handled.
  2. Re-ingest — proceed anyway (e.g., you want fresh takeaways or a merge).
  3. Investigate — tell me what's different and I'll decide after.
```

On **skip**: write a stub to `vault/.skipped/<slug>.md` with
`reason: "duplicate"` and `folded_into: <matched-source-slug>`, log a
`note` entry via `append_log.py`, exit. On **re-ingest**: continue, and
note the override in the dispatch prompt at step 12. On **investigate**:
stay in step 4 until the user decides.

If no match, continue.

### 5. Pre-screen for relevance and quality

```bash
python <wiki>/.scripts/pre_screen.py --wiki <wiki> --source <raw-file> --json
```

The script checks `source_tags` overlap with the wiki's tag vocabulary,
keyword density against domain terms, promotional signal patterns, and
author watchlist. Verdict drives flow:

- **`pass`** (exit 0) → continue to step 6. No user output needed.
- **`warn`** (exit 1) → present reasons + override option:

  ```text
  Pre-screening flagged this source:
  - <reason 1>
  - <reason 2>
  Tag overlap: <N> | Domain score: <X%> | Promo signals: <N>

  Options:
    1. Proceed anyway — continue to takeaway pass.
    2. Skip — record as skipped and move on.
  ```

  On **proceed**: continue. On **skip**: use the skip protocol (below) and
  exit.

- **`auto-skip`** (exit 2) → present reasons and recommend skip:

  ```text
  Pre-screening recommends SKIP:
  - <reason 1>
  Tag overlap: <N> | Domain score: <X%> | Promo signals: <N>

  Skipping unless you override. Say "proceed" to force the takeaway pass.
  ```

  On silence or "skip": skip protocol + exit. On "proceed": continue, note
  the override in the dispatch prompt at step 12.

**Skip protocol (used here and in step 11).** Wiki-side skip stubs go in
`vault/.skipped/<slug>.md`. Reasons accepted:

- Clipper-upstream set: `paywall-fragment` / `404` / `spam` / `duplicate`
- Wiki-downstream additions: `off-domain` / `promotional` / `shallow` / `already-folded` (the last with a `folded_into: <wiki-page-slug>` field).

After writing the stub:

```bash
python <wiki>/.scripts/append_log.py note --title "skipped: <slug>" --note "reason: <reason>" --wiki <wiki>
```

Exit without dispatching.

The pre-screener is a **fast deterministic filter** (no LLM tokens, runs
in milliseconds). It catches clearly off-domain and heavily promotional
content before the takeaway pass spends time reading. It is NOT a
substitute for the takeaway pass — borderline cases still need human
judgment at step 11.

### 6. Read the raw source file

Read the raw vault file in full. This is the only main-session source
read. The agent will read it again in its forked context to extract
evidence sections — that's intended; preview alone is insufficient there.

### 7. Discover related pages

Find existing wiki pages that overlap the source's vocabulary or themes.
Use BOTH lexical and semantic discovery:

#### 7a. Lexical hits (Grep tool)

From the source, derive a candidate list of:

- Slug-shaped names (titles, named entities, tools, frameworks, models)
- Acronyms and alternate names
- Tag/alias keywords likely to overlap (e.g., `claude-code`, `mcp`, `agent-skills`, `prompt-caching`)

Run the Grep tool with `output_mode: "files_with_matches"` over `wiki/`. A
single Grep call with an alternation pattern (`pattern1|pattern2|...`)
covers many candidates at once. The page directories you care about are
`wiki/pages/sources/`, `wiki/pages/concepts/`, `wiki/pages/entities/`, `wiki/pages/comparisons/`,
`wiki/pages/syntheses/`.

> **Glob caveat.** The Grep tool's `glob` parameter does **not** expand
> brace alternation — `glob: "{sources,concepts,entities}/*.md"` returns
> "No files found", silently. Either omit `glob` (set `path: "wiki/"` and
> let the alternation pattern in your regex do the work — page-directory
> matches will surface naturally), or run separate Grep calls per
> directory. Do not rely on `{a,b}` brace syntax in `glob`.

#### 7b. Semantic anchors (`hybrid_search.py`)

Pick 2–3 of the source's main themes (from its framing — title, opening
claim, recurring concepts) and pass them as a JSON array in **one** call,
not separate calls. The script's Phase 2 multi-query mode (`--queries-json`)
runs BM25 + dense per query and RRF-fuses all rank lists in a single ONNX
model load — three separate calls would pay the model spin-up cost three
times for the same fused result.

The `--queries-json` flag takes a **file path** or `-` for stdin — it does not accept a literal JSON string as the argument. **Always use the file-path form**, not stdin.

```bash
TMP=$(mktemp --suffix=.json)
echo '["<theme1>","<theme2>","<theme3>"]' > "$TMP"
python <wiki>/.scripts/hybrid_search.py <wiki> --queries-json "$TMP" --top-k 5
rm "$TMP"
```

This catches synonym/paraphrase overlap that grep can't (e.g., the source
talks about "context windows" and the wiki has a page on "prompt caching"
with overlapping concerns). Cap at 3 themes; the goal is anchoring, not
coverage.

#### 7c. Read the matched pages

Combine the unique hits from 7a + 7b. Cap at ~10–15 pages total. For each
hit, read **only** the frontmatter and the section relevant to the
overlap (use the Read tool's `offset`/`limit` parameters). Do not read
full pages; the goal is enough context to know what each page currently
says about the relevant topic.

> **FORBIDDEN by default:** Read() of `wiki/pages/indexes/index-concepts.md` or
> `wiki/pages/indexes/index-entities.md`. At current scale these are
> 25–30K tokens combined; reading them per ingest is a token-cost
> regression. The grep+hybrid pattern above is strictly cheaper and has
> better recall (semantic).
>
> **Rare fallback:** if 7a + 7b yield empty hits for a clearly on-topic
> source, the wiki's vocabulary may be a poor match for the source's
> framing (taxonomy gap). In that case, Read() **one** sub-index
> (`index-concepts.md` OR `index-entities.md`, not both) to scan
> categorically. When the fallback fires, flag it explicitly in the
> takeaway output so the user knows discovery was less precise:
> `> Note: discovery fallback — read full <sub-index> because grep + hybrid yielded no hits.`

### 8. Analyze the source

Identify, in this exact list:

- **Main argument** — one sentence
- **Key claims** — 5–10 bullet facts or arguments
- **Concepts mentioned** — things that might deserve a concept page (or fold into existing concept pages)
- **Entities mentioned** — specific people, tools, services, models, organizations, products
- **Comparisons made** — places where the source explicitly contrasts X vs Y
- **Tensions with existing knowledge** — claims that might contradict pages found in step 7. For each Tension, name the matched page from step 7 it tenses against.

This is the analysis the agent used to do in its old step 2. It now lives
here, once, with the page context from step 7 already in hand.

### 9. Contradiction probe

For each Tension from step 8, read the matched page from step 7 closely
and ask: "Does the new source contradict the existing claim?"

**Temporal facts are NOT contradictions.** For time-varying values (star
counts, version numbers, user counts, dates, growth metrics, employment
status, prices), the new figure usually reflects a later capture, not a
conflict. Record both as dated snapshots in the page body — e.g.,
"100k stars (Feb 2026, per [[source-a]]); 247k stars (Mar 2026, per
[[source-b]])" — and do **not** count it as a contradiction. Reserve the
count for genuine factual conflicts (e.g., "tool X supports feature Y" vs
"tool X does not support feature Y" at the same point in time).

Count remaining genuine contradictions. Carry the list forward — the
takeaway block in step 11 surfaces them and the dispatch prompt in step 12
hands them to the agent for callout placement.

### 10. Hard-stop check (>5 contradictions)

If genuine contradictions exceed 5, **STOP** before presenting takeaways.
Surface to the user:

```text
HARD STOP: This source would contradict N existing pages (threshold: 5).
This is paradigm-shifting material. Let's talk through it before ingesting.

Contradicted pages:
- [[page-1]] — <existing claim> vs <new claim>
- [[page-2]] — ...
```

Do not proceed unless the user explicitly tells you to override.

### 10.5. Pre-write secret scan

Run the deterministic stdlib scanner against the source file:

```bash
python wiki/.scripts/check_secrets.py --wiki wiki "<source-path>"
```

Pure stdlib regex; no model dependency. Output is JSON with a `findings`
list. Each finding carries `severity` (`high` for known credential
prefixes — GitHub PATs, AWS keys, Stripe / OpenAI / Anthropic keys, etc.;
`low` for ambiguous high-entropy strings inside fenced code blocks),
`pattern`, `line`, `redacted` token preview, and `context` snippet.

**On `high` findings:** surface the redacted match list in the takeaway
block under § Secrets scan with **HALT** semantics — the user must
acknowledge each high finding (`accept all`, `accept N,M`, or describe
how to redact in vault) before dispatch. The clipper rule for not
storing third-party credentials (`vault/.clipper-rules.md`) is the
upstream prevention; this scan is forward-insurance for when that rule
fails.

**On `low` findings only:** include them in § Secrets scan as a soft
warning — no halt; takeaway-gate proceeds normally. The user can opt to
investigate or skip.

**On no findings:** include § Secrets scan with `clean` for affirmative
visibility; absence-of-section is harder to audit than a positive
all-clear.

This is the parser-side application of [[lethal-trifecta]]'s
"sanitize the untrusted-content leg" mitigation — categorically reliable
for known credential prefixes (where regex is sufficient) but not a
substitute for the clipper-side discipline that keeps secrets out of the
vault in the first place.

Sidecar config: `wiki/.meta/secret-patterns.json` (optional) extends the
built-in pattern set; documented in the `check_secrets.py` module
docstring (`python wiki/.scripts/check_secrets.py --help`).

### 11. Present takeaways and WAIT for free-form approval

Present in the main chat in this exact format:

```markdown
## Ingest plan for <source title>

### Key takeaways
1. <takeaway 1 — a specific claim or finding from the source>
2. <takeaway 2>
3. <takeaway 3>

### Proposed operations
- CREATE pages/sources/<slug>.md — source summary
- CREATE pages/concepts/<slug>.md — <one-line reason>
- EDIT pages/entities/<existing-slug>.md — adding <what>
- EDIT pages/concepts/<existing-slug>.md — adding <what>
- <etc>

> **Single-source CREATE annotation.** Any non-source CREATE entry (i.e.,
> a new pages/concepts/, pages/entities/, pages/comparisons/, or pages/syntheses/ page) where the
> source for the new page is just this single ingest must be annotated
> `— single source; recommend verify: true` so the user can decide at the
> gate whether to (a) approve standalone with `verify: true`, (b) approve
> standalone as canonical (no verify), or (c) fold into an existing page
> instead. Per wiki/AGENTS.md "Single-source standalone pages" rule.
> Example: `CREATE pages/concepts/agent-studio.md — Google's low-code agent
> authoring environment; single source; recommend verify: true`.

> **Verbatim-citation requirement for new entity pages.** Every
> `CREATE pages/entities/<slug>.md` line must include a block-quote line below
> it carrying the **verbatim phrase from the source** that contains the
> entity name. This is the fabrication-prevention gate — if the user sees
> "no verbatim phrase found in source," they know the agent is inventing
> the name. If you cannot find a verbatim phrase containing the entity
> name, **do not list the CREATE** — fold the reference into an existing
> page or omit it entirely. Concepts, comparisons, and syntheses are
> exempt (verbatim quotes encouraged but not required, since they
> aggregate across sources). Per wiki/AGENTS.md "Verbatim citation for
> new entity pages" rule.
>
> Example takeaway-block snippet:
>
> ```
> ### Proposed operations
> - CREATE pages/entities/agent-studio.md — Google's low-code authoring environment
>   > "Agent Studio is Google's low-code visual authoring environment for
>   > AI agents on Google Cloud" — verbatim from source
>   — single source; recommend verify: true
> - CREATE pages/entities/adk-cicd.md — ADK CI/CD skill
>   > NO VERBATIM PHRASE FOUND — fold into [[google-adk]] § Skills instead
> ```

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

### Contradictions detected
- [[page-name]] — older claim: <X>; new claim: <Y>
- (or: "None.")

### New categories proposed
- <kebab-case name> — <why existing categories don't fit>
- (or: "None.")

### Secrets scan
- (clean — no findings)
- (OR: HALT — high findings; user must acknowledge)
  - line 42 `github_pat_classic`: `ghp_aa...XX` — `export TOKEN=ghp_aa...XX`
  - line 87 `aws_access_key`: `AKIAIO...LE` — `AWS_KEY=AKIAIO...LE`
- (OR: low findings only — soft warning, takeaway-gate proceeds normally)
  - line 51 `high_entropy_in_code_block`: `4e0d8b...5e` — likely a hash, review

Proceed? Anything to emphasize, de-emphasize, or skip?
```

**Wait for the user's explicit reply before dispatching.** Acceptable
replies:

- `proceed` / `yes` / `go` → dispatch as-is.
- `proceed with emphasis on X` / `skip the Y section` → bake into the dispatch prompt's "User emphasis / skip hints" block.
- `skip this source` / `abort` → use the skip protocol from step 5 with the appropriate reason (`off-domain` / `promotional` / `already-folded` / `shallow`); exit without dispatching.
- `redo — you misread Z` → adjust steps 8–11 and re-present.

If the user is silent, ask once more; do not assume tacit approval.

### 12. Dispatch the wiki-ingestor agent

**Before** calling the Agent tool, record the main-session wall-clock
start time (used for the telemetry `--duration-ms` fallback in step 13 if
the `<usage>` block is missing).

Model selection (priority order — first match wins):

1. **`"sonnet"`** if the raw vault file's frontmatter has `quality: listicle` OR `word_count` from the step-3 brief is < 1200.

2. **`"opus"`** otherwise.

> Overview regeneration was previously bundled into the ingestor (every 5th ingest, dispatched as opus). It now lives in the dedicated `/wiki-overview` skill and is triggered separately — no longer a model-selection input here. The ingestor never edits `overview.md`.

Use the Agent tool:

- **subagent_type:** `"wiki-ingestor"`
- **model:** `"sonnet"` or `"opus"` per above
- **description:** `"Wiki ingest — <source title>"`
- **prompt:**

```text
You are the wiki-ingestor subagent. Find your full playbook by running
Glob for '**/agents/wiki-ingestor.md' and read it in full before starting.

Source: <raw vault path>
Wiki root: <absolute path to wiki/>

Approved plan:
<paste the entire block from step 11 verbatim — Key takeaways,
Proposed operations, Contradictions detected, New categories proposed>

User emphasis / skip hints:
<paste any guidance the user added on approval, or "none">

Verify-clearing decisions:
<for each `### verify: true re-evaluation` block in the takeaway, paste one
line per page in the form:
  <slug> — <Y | N | P: "<narrowed callout text>">
Or "none" if the takeaway didn't surface any verify-clearing blocks.>

Contradiction-resolution decisions:
<for each `### Contradiction resolution` block in the takeaway, paste one
line per pair in the form:
  <slug-a>|<slug-b> — <a_wins | b_wins | both_stand | defer>
                      [aggregator: <slug>] (optional, only for a_wins/b_wins
                      when the aggregator page is not obvious — name the
                      page that should carry the Resolved callout)
Or "none" if the takeaway didn't surface any contradiction-resolution blocks.>

Source brief:
<paste the JSON from step 3 verbatim>

Honor the approved plan. The plan's Contradictions list tells you which
pages need > [!WARNING] Contradiction Detected: callouts and what to put
in them. New categories were already approved by the user — apply them as
the plan prescribes.

If during writing you discover NEW contradictions the probe missed, follow
your playbook's late-contradiction handling (≤5: per-page callout,
continue; >5: HALT, rollback via git restore + git clean, return without
committing).

Return your structured report.
```

Notes on the prompt shape:

- **Do not paste the full source content.** The agent reads the raw file directly via the `Source:` path. Pasting it doubles token cost in the fork.
- **Do not paste page-discovery results.** The plan's CREATE/EDIT lines name the exact pages to touch — that's the only discovery output the agent needs. The agent does NOT re-discover.

### 13. Log ingest telemetry

Parse two blocks from the Agent tool's return and write one row to
`<wiki>/.meta/ingest_log.jsonl`. Schema in the `append_ingest_log.py`
module docstring (`python wiki/.scripts/append_ingest_log.py --help`).

**Block A — `<usage>` metadata** the Agent tool appends after the report:

```
<usage>total_tokens: <N>, tool_uses: <N>, duration_ms: <N></usage>
```

The block may include additional fields the Agent tool surfaces natively
(`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`,
`output_tokens`); capture each independently rather than relying on a
single multi-group regex, so format drift doesn't lose the row. For each
field name in `{total_tokens, input_tokens, cache_creation_input_tokens,
cache_read_input_tokens, output_tokens, tool_uses, duration_ms}`, run
`<field>:\s*(\d+)` against the `<usage>...</usage>` content and capture
when present.

> **Known regex quirk** (documented + accepted). The `input_tokens:\s*(\d+)`
> pattern matches as a substring inside `cache_creation_input_tokens` /
> `cache_read_input_tokens`. Has not bitten production because the Agent
> tool today emits only `total_tokens` / `tool_uses` / `duration_ms`. If
> the harness ever starts emitting `cache_*_input_tokens` without also
> emitting `input_tokens`, the regex will return the longer field's value
> as if it were `input_tokens`. Fix when it bites: anchor with negative
> lookbehind — `(?<![a-z_])input_tokens:\s*(\d+)`. The contract + quirk are
> pinned by `wiki/.scripts/test_usage_parsing.py` (six cases, stdlib
> unittest); update spec and test together.

If the entire block is missing or all field regexes fail, pass empty
strings for the cost fields and use the main-session wall-clock from
step 12 as the `--duration-ms` fallback. Telemetry is observational, not
a contract — null fields are preferable to losing the row. The
`cache_creation_input_tokens` field in particular is the load-bearing
slice for measuring SessionStart-hook cache savings.

**Block B — the agent's structured report** (from its step 8). Extract:

- `pages_created` — comma-join slugs from the `Created:` list (without `.md`)
- `pages_updated` — comma-join slugs from the `Updated:` list
- `contradictions_flagged` — total integer (plan + late-found) after `Contradictions recorded:`
- `commit_hash` — short hash after `Commit:`

Carry forward from earlier steps:

- `source_path` — from step 1
- `source_type` — the raw vault file's `source_type:` frontmatter value
- `source_word_count` — `word_count` from the step-3 brief

Append the row:

```bash
python <wiki>/.scripts/append_ingest_log.py --wiki <wiki> \
  --source-path "<source_path>" \
  --source-type <source_type> \
  --source-word-count <N> \
  --pages-created "<comma-sep slugs>" \
  --pages-updated "<comma-sep slugs>" \
  --contradictions-flagged <N> \
  --total-tokens <N or ""> \
  --input-tokens <N or ""> \
  --cache-creation-input-tokens <N or ""> \
  --cache-read-input-tokens <N or ""> \
  --output-tokens <N or ""> \
  --tool-uses <N or ""> \
  --duration-ms <N> \
  --commit-hash <short-hash>
```

If the appender exits non-zero, mention it briefly in the relay and move
on. Do NOT retry; do NOT fail the ingest on telemetry failure.

**Fold telemetry into the agent's commit (when the appender succeeded).**
The appender writes to `wiki/.meta/ingest_log.jsonl` AFTER the agent's
commit — left as-is, the row stays dirty until a later session backfills
it, misattributing ingest N's telemetry to ingest N+1's commit. Amend
the agent's commit to fold the row in:

```bash
if ! git diff --quiet wiki/.meta/ingest_log.jsonl wiki/log.md wiki/.meta/lint_log.jsonl; then
    git add wiki/.meta/ingest_log.jsonl wiki/log.md wiki/.meta/lint_log.jsonl
    git commit --amend --no-edit
fi
```

The diff-quiet gate is defensive (no-op when the appender failed or when
nothing actually changed). HEAD at this point is the agent's commit, or
the most-recent fix-up commit when the README-ingest pattern produced one
(per step 8 of the ingestor playbook) — both are correct amend targets.
Personal-repo amend policy applies (see [`vault/.wiki-identity.md`](../../../vault/.wiki-identity.md) Obsidian commit policy).

### 13.5. Tick the triage batch checkbox (when this source came from a triage batch)

If this ingest is part of an active triage batch, record per-article
progress so a mid-batch stop is never mistaken for an unstarted batch. This
is a no-op for one-off ingests (the grep in sub-step 2 returns nothing).

1. **Find the active triage file** — the newest `dev/ingest-triage-*.md`
   whose frontmatter `status:` is `active` (ignore `*-archived*` /
   `*-superseded*`). None found → skip the whole step.
2. **Locate the unchecked article line** for this source's slug
   (`<suggested_slug>` from the step-3 brief, e.g.
   `2026-05-16-claude-agent-sdk-iterative-refinement-loops`):
   ```bash
   grep -n "^- \[ \] \*\*\`<slug>.md\`\*\*" <triage-file>
   ```
   No match → the source wasn't a batch article (one-off, or already
   ticked); skip the rest of the step.
3. **Tick it** — flip that single line's `- [ ]` to `- [x]` with the Edit
   tool (exact-match the one line; never `replace_all`).
4. **Batch-completion roll-up** — if the article's `## Batch N` body now has
   zero remaining `- [ ]` lines, also flip that batch's
   `- [ ] **Batch N** — …` line in the `## Status checklist` to `- [x]`.
5. **Commit the tick on its own** — the triage file lives in `dev/`,
   separate from the wiki content the agent already committed, so don't fold
   it into the ingest commit:
   ```bash
   git add <triage-file>
   git commit -m "triage: tick <slug> (Batch N — <done>/<total>)"
   ```
6. **All-batches-done?** If this tick completed the file's last unchecked
   batch box, the triage file's own Cleanup trigger applies — surface it in
   step 14's relay ("Batch N complete; triage file ready to remove") rather
   than auto-deleting here. Deletion stays a deliberate step.

The wake-up drill's "Batch X next / First up" pointer reads these checkboxes
(via `drill_state.py`, which skips ticked articles and self-heals a missed
tick by also skipping any slug that already has a source page). Ticking here
is what keeps that pointer honest across sessions.

### 14. Relay the result

Summarize the agent's report to the user in the main chat:

- Pages created (count and paths)
- Pages updated (count, paths, short notes)
- Contradictions recorded (split: plan-listed + late-found, if any)
- Overview refreshed (yes/no)
- Commit hash
- Mechanical lint findings (the key non-zero counts, or "clean")
- Telemetry row appended (ok / failed — one line)
- Triage progress (only if this source was a batch article): ticked `<slug>` — Batch N now <done>/<total>; flag batch or whole-triage completion when reached.
- Cadence reminder if the agent's report flagged a 10th/20th/30th post-lint ingest — suggest `/wiki-lint` now.

If the agent returned a HALT (late contradictions exceeded 5, rollback
fired), surface that prominently and offer next steps: re-run the
takeaway pass with the new contradictions in scope, or escalate as
paradigm-shifting material that warrants discussion.

## Rules

- **The agent is invoked exclusively through this skill.** Capture chains here, not to the agent. Do not dispatch the agent from any other context.
- **Approval gate stays in main session.** The user's reply pattern is free-form ("emphasize X, skip Y"); structured-question tools don't capture it. Do not delegate the gate to the agent via `AskUserQuestion`.
- **One agent per file.** Never batch multiple files into a single ingestor call — it bloats context and causes cross-contamination.
- **`all new` runs sequentially.** Run takeaway + approval + dispatch for each file in order. Do not parallelize — git commits, `log.md` append, and index regeneration race.
- **Subagent fallback.** If the `wiki-ingestor` subagent is unavailable, fall back to running its playbook inline — but only the agent's steps 1–8 (its execute-and-commit logic). Do not re-do the SKILL's steps 3–11 work; the plan from step 11 is sufficient.
- **Follow-up questions** about an ingest can be answered from the subagent's returned report without re-dispatching.
- **Discovery is grep + hybrid_search, not Read.** Full-index Read() is the rare fallback flagged in step 7c — never the default.

## Why this structure

The skill owns the entire pre-write pipeline because:

1. **Free-form approval needs main-thread conversational input.** The user replies with prose ("skip the LangChain section, emphasize the cost angle"); structured tools like `AskUserQuestion` would force discrete choices and lose expressiveness.
2. **Discovery and analysis benefit from main-context state.** `wiki/AGENTS.md` is auto-loaded into the main session (via `wiki/CLAUDE.md`'s `@-include`); the user's prior conversation is in scope; spawning a subagent for the cheap pre-flight is overkill.
3. **The agent stays a pure executor.** Single source of truth per concern: discovery + analysis + probe = SKILL; writes + commit + log + lint = agent. No overlap, no redundancy.
4. **Capture chains here.** `/wiki-capture` is a thin onramp — it lands the file in vault/ and hands the path to this skill. The agent is never invoked directly.
