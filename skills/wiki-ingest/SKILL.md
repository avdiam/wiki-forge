---
name: wiki-ingest
description: Compile a vault source file into wiki pages. Owns the entire pre-write pipeline — pre-process + dedup probe + pre-screen + source read + discovery (grep + hybrid_search) + analysis + contradiction probe + takeaway approval gate — then dispatches the wiki-ingestor agent with a complete approved plan to execute the writes. The agent is invoked exclusively by this skill; capture chains here, never to the agent. Use when the user runs /wiki-ingest, says "ingest this file", "process this source", or after wiki-capture chains into it.
argument-hint: "<vault-path-or-all-new>"
---

# wiki-ingest (orchestrator + analyzer + approval gate)

*Architecturally: inline orchestration with Agent-tool dispatch at step 12.*

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
(run `python "${CLAUDE_PLUGIN_ROOT}/scripts/lint_wiki.py" wiki --only source_backlog`).

> [!NOTE]
> If `all new` finds more than 10 unprocessed files AND the wiki has fewer
> than 20 existing pages, warn: "Your wiki is still young. Consider
> ingesting 5–10 sources first to establish the taxonomy, then batch the
> rest. Large initial batches produce shallow cross-references because
> there are no existing pages to connect to."

### 2. Locate the wiki

Find `wiki/CLAUDE.md` in the current directory or upward. If none found,
tell the user, suggest `/wiki-init`, stop.

### 3. Pre-flight (one call)

Every deterministic pre-write check runs in one script: the ingest-exclusion
rule, filename normalization, the `ingest_source` brief, the content-hash
dedup probe, `pre_screen`, `check_secrets`, the model pick and the triage-batch
lookup.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/ingest_preflight.py" --wiki <wiki> --source "<raw-file>"
```

- **Exit 2** means the input is bad or excluded (a `.`/`_`-prefixed vault segment, or `not_a_source: true`). Surface `error` and stop. Excluded files are refused before any rename.
- **Exit 1** means a slug collision on rename. Surface `error` and stop; collision resolution is a manual call.
- **Exit 0** returns a compact JSON report. From here on, use `source` as `<raw-file>`: it is the canonical name if the script renamed the file. Keep `brief_path`, `model`, `word_count` and `secrets` for later steps.

Then handle each entry in `gates`, in this order. An empty list means go straight to step 6.

- **`renamed`.** A non-canonical Web Clipper name was renamed to `YYYY-MM-DD-<kebab-slug>.md` (with `git mv` if tracked). Contents are untouched. Report it in the step-11 block as `renamed: <renamed_from> → <source>`.
- **`merge_mode`.** The source was previously ingested (`existing_summary`). Ask whether to proceed, which triggers the agent's APPEND-`## Re-ingest <date>` mode, or skip. On skip, log a `note` via `append_log.py` and exit.
- **`duplicate`.** Go to step 4.
- **`pre_screen_warn` / `pre_screen_auto_skip` / `pre_screen_error`.** Go to step 5.
- **`secrets_high` / `secrets_low`.** These are handled at the step-11 gate (step 10.5).

The script writes the full brief to `brief_path`. The ingestor reads it from
that path, and step 13 consumes and deletes it. Never paste or retype it.

### 4. Duplicate gate (`duplicate`)

`duplicates` lists source pages whose `source_hash` equals this file's
`sha256_short`. Present:

```text
Possible duplicate: <raw-file> has the same content hash (<sha256_short>) as
<matched-source-page>. The filename / URL / date may differ but the bytes
are identical.

Options:
  1. Skip — don't re-ingest; record the vault file as handled.
  2. Re-ingest — proceed anyway (e.g., you want fresh takeaways or a merge).
  3. Investigate — tell me what's different and I'll decide after.
```

- **Skip:** write a stub to `vault/.skipped/<slug>.md` with `reason: "duplicate"` and `folded_into: <matched-source-slug>`, log a `note` via `append_log.py`, and exit.
- **Re-ingest:** continue, and note the override in the dispatch prompt at step 12.
- **Investigate:** stay in step 4 until the user decides.

### 5. Pre-screen gate

`pre_screen` carries the verdict and its reasons. It checks tag overlap with
the wiki's vocabulary, domain keyword density, promotional patterns and the
author watchlist.

- **`pre_screen_warn`:** present the reasons and offer to proceed or skip:

  ```text
  Pre-screening flagged this source:
  - <reason 1>
  Tag overlap: <N> | Domain score: <X%> | Promo signals: <N>

  Options:
    1. Proceed anyway — continue to takeaway pass.
    2. Skip — record as skipped and move on.
  ```

- **`pre_screen_auto_skip`:** present the reasons and recommend skipping: "Skipping unless you override. Say "proceed" to force the takeaway pass." Skip on silence or "skip". On "proceed", note the override in the dispatch prompt at step 12.
- **`pre_screen_error`:** surface the reason and stop.

**Skip protocol (used here and in step 11).** Skip stubs go in
`vault/.skipped/<slug>.md`. Accepted reasons:

- Capture-upstream set: `paywall-fragment` / `404` / `spam` / `duplicate`
- Wiki-downstream additions: `off-domain` / `promotional` / `shallow` / `already-folded` (the last with a `folded_into: <wiki-page-slug>` field).

After writing the stub:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" note --title "skipped: <slug>" --note "reason: <reason>" --wiki <wiki>
```

Exit without dispatching. The pre-screener is a fast deterministic filter,
not a substitute for the takeaway pass: borderline cases still need human
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
PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/hybrid_search.py" <wiki> --queries-json "$TMP" --top-k 5
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

### 10.5. Secret scan (from pre-flight)

`secrets` in the step-3 report carries the `check_secrets.py` result: counts
of `high` and `low` findings, plus up to 5 redacted findings. If either count
is non-zero, read [references/secret-scan.md](references/secret-scan.md).
`high` findings HALT at the step-11 gate until the user acknowledges each one;
`low` findings alone are a soft warning; no findings is reported as `clean`
under § Secrets scan. To see every finding, rerun
`python "${CLAUDE_PLUGIN_ROOT}/scripts/check_secrets.py" --wiki <wiki> "<raw-file>"`.

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

<conditional sections from references/plan-conditional.md go here, if any>

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

**Conditional sections.** Before assembling the block, grep each EDIT target's
frontmatter for `^verify: true` and for a non-empty `contradicts:` list. If either
matches, read [references/plan-conditional.md](references/plan-conditional.md) and
insert its sections where the template marks them. Most ingests match neither.

**Wait for the user's explicit reply before dispatching.** Acceptable
replies:

- `proceed` / `yes` / `go` → dispatch as-is.
- `proceed with emphasis on X` / `skip the Y section` → bake into the dispatch prompt's "User emphasis / skip hints" block.
- `skip this source` / `abort` → use the skip protocol from step 5 with the appropriate reason (`off-domain` / `promotional` / `already-folded` / `shallow`); exit without dispatching.
- `redo — you misread Z` → adjust steps 8–11 and re-present.

If the user is silent, ask once more; do not assume tacit approval.

### 12. Dispatch the wiki-ingestor agent

Model: use `model` from the step-3 report. It is `"sonnet"` when the raw
file's frontmatter has `quality: listicle` or `word_count` < 1200, and
`"opus"` otherwise. The ingestor never edits `overview.md`; overview
refreshes run through `/wiki-overview`.

Use the Agent tool:

- **subagent_type:** `"wiki-forge:wiki-ingestor"`
- **model:** `"sonnet"` or `"opus"` per above
- **description:** `"Wiki ingest — <source title>"`
- **prompt:**

```text
Source: <raw vault path>
Wiki root: <absolute path to wiki/>
References: <this skill's base directory>/references

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

Source brief: <brief_path from step 3>

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

- **`References:`** is this skill's `references/` directory as an absolute path. The agent Reads rare-branch files from it on demand (agents can't rely on `${CLAUDE_PLUGIN_ROOT}` expanding in their shell).
- **Pass the brief by path.** The agent reads the JSON file itself; pasting it costs output tokens and invites transcription errors.
- **Do not paste the full source content.** The agent reads the raw file directly via the `Source:` path. Pasting it doubles token cost in the fork.
- **Do not paste page-discovery results.** The plan's CREATE/EDIT lines name the exact pages to touch — that's the only discovery output the agent needs. The agent does NOT re-discover.

### 13. Close the operation (telemetry + triage tick)

One call parses the agent's report, logs the `ingest_log.jsonl` row, makes the
telemetry commit and ticks the triage checkbox. Pipe **every** Agent result
for this ingest into it verbatim: the structured report and the `<usage>`
block. For a background dispatch, use the completion notification and wait
for it. Use a quoted heredoc, so nothing in the report is shell-expanded:

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/close_operation.py" ingest --wiki <wiki> \
  --agent-id <agent-id> --brief <brief_path> <<'RESULT'
<the agent's full result, verbatim, including <usage>...</usage>>
RESULT
```

What the script does, so you don't repeat it by hand:

- **Created/updated slugs** come from `git show --name-status` on the reported `Commit:`, unioned with the report's lists, as bare stems.
- **Cost fields** come from the `<usage>` block, enriched from the subagent transcript. Duration falls back to the transcript's turn spans when no block arrived; `--duration-ms` overrides both.
- **Source fields:** `source_type` comes from the raw file's frontmatter (missing or invalid values log `article`, or `paper` under `Research Papers/`, with a warning). `word_count` comes from the brief.
- **Telemetry commit.** The row (plus the ingestor's `log.md` entry, mechanical `lint_log` row and any `corroborate_log` / `contradiction_log` backfill stamps) gets its own commit, `ingest: telemetry for <hash>`, with explicit paths. It never amends the agent's commit: an amend rewrites the hash the row records (s132).
- **Triage tick.** If the source is an unchecked article in the active `dev/ingest-triage-*.md`, the script ticks it, rolls up the batch box when the batch is done, and commits `triage: tick <slug> (Batch N — d/t)`. Deleting a finished triage file stays a deliberate step, so `all_complete` is only reported.

Output is one JSON line: `row`, `content_commit`, `telemetry_commit`,
`created`, `updated`, `contradictions`, `usage` (with `non_cached`),
`triage`, `warnings`.

- **Exit 2** (no `Commit:` line: a HALT/rollback) logs nothing. That is correct; relay the HALT.
- **`warnings`** (an appender failure, for example) are reported in one line. Never retry, and never fail the ingest on telemetry.

### 14. Relay the result

Summarize the agent's report to the user in the main chat:

- Pages created (count and paths)
- Pages updated (count, paths, short notes)
- Contradictions recorded (split: plan-listed + late-found, if any)
- Overview refreshed (yes/no)
- Commit hash
- Mechanical lint findings (the key non-zero counts, or "clean")
- Telemetry row appended (`row` ok, or the `warnings` — one line)
- Triage progress (only when `triage` is non-null): ticked `<slug>` — Batch N now <done>/<total>; flag `batch_complete` / `all_complete` ("triage file ready to remove").
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
- **Subagent fallback.** If the `wiki-ingestor` subagent is unavailable, fall back to running its playbook inline — but only the agent's execute-and-commit steps. Do not re-do the SKILL's steps 3–11 work; the plan from step 11 is sufficient. Close with step 13, passing the inline report and `--duration-ms`.
- **Follow-up questions** about an ingest can be answered from the subagent's returned report without re-dispatching.
- **Discovery is grep + hybrid_search, not Read.** Full-index Read() is the rare fallback flagged in step 7c — never the default.

## Why this structure

Design rationale: [references/design.md](references/design.md).
