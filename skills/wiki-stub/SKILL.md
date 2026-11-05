---
name: wiki-stub
description: >-
  Create a low-confidence placeholder page for a slug — three patterns. `hub` anchors a slug accumulating ≥10 incoming wikilinks but no dedicated source yet. `single-source` codifies the in-band single-source CREATE path. `planned` declares a user-pre-empted research target with zero sources. All three pair `verify: true` with a verbatim callout the `check_drifted_stubs` lint depends on. Use when the user says /wiki-stub <slug>, "stub this slug", "create a placeholder for X", or "list stub candidates".
argument-hint: "<slug> [--type concept] [--pattern hub|single-source|planned] [--source <vault-path>] [--reason \"<text>\"] | --list"
disable-model-invocation: true
---

# wiki-stub

The codified path for creating low-confidence placeholder pages. Three
patterns under one skill — `hub`, `single-source`, `planned`. All write
`verify: true` + `stub_origin: <pattern>` + a verbatim callout per the
"Stub callout templates" contract (the canonical
callout text lives in this skill; `wiki/AGENTS.md` keeps only the
single-source callout the ingestor writes in-band).

The skill never silently writes — every pattern surfaces a confirmation
prompt before touching `wiki/`. The user can abort at the prompt.

*Architecturally: inline tool-orchestration. Main-session only — no subagent dispatch.
The skill calls `list_unresolved_hubs.py` for inbound counts and writes
the page inline.*

## Workflow

### 1. Locate the wiki

Find the wiki root (`wiki/CLAUDE.md`) in the current directory or upward.
If none, tell the user and suggest `/wiki-init`.

### 2. Parse arguments

| Form | Meaning |
|---|---|
| `<slug>` | Hub pattern (default). Run inbound count, require ≥10 (soft-warn 5-9, hard-block 0). |
| `<slug> --pattern hub` | Same as above, explicit. |
| `<slug> --pattern single-source --source <vault-path>` | Single-source pattern. `--source` is required. |
| `<slug> --pattern planned --reason "<one-liner>"` | Planned pattern. `--reason` is required. |
| `<slug> --type concept` | Override default `pages/entities/` routing → write to `pages/concepts/` instead. Composable with any pattern. |
| `--list` | List candidate slugs from the unresolved-hub scan (informational; doesn't create). |

Normalize `<slug>` — strip `[[...]]` brackets if present; the canonical
form is bare kebab-case.

### 3. Branch by pattern

Dispatch to one of the four workflows below. `--list` is its own
informational path.

---

## Hub pattern (default)

#### H1. Inbound check

Run `list_unresolved_hubs.py` for the target slug:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/list_unresolved_hubs.py" wiki --slug <slug>
```

The script returns JSON:

```json
{"slug": "<slug>", "inbound": <N>, "referring_pages": [...], "resolves": <bool>}
```

Decision table:

| Condition | Action |
|---|---|
| `resolves: true` | Hard-block: page already exists. Surface the existing path (`<page-type>/<slug>.md`) and suggest `/wiki-ingest` to extend it instead. Stop. |
| `inbound == 0` | Hard-block: nothing to anchor. Tell the user no incoming wikilinks were found for `<slug>`. Stop (or suggest `--pattern planned` if the user wants a research target). |
| `inbound < 5` | Hard-block: below the soft-warn floor. Tell the user the slug has only `<N>` incoming wikilinks; hub stubs are reserved for slugs with measurable graph pressure. Stop. |
| `5 <= inbound < 10` | Soft-warn: "Only `<N>` incoming wikilinks — hub stubs are usually justified at ≥10. Proceed anyway? (y/N)". Wait for explicit y. |
| `inbound >= 10` | Proceed silently. |

#### H2. Prompt for page metadata

Single structured prompt — surface all five fields at once:

```
Creating hub stub for `<slug>` (anchoring <N> incoming wikilinks).

Fill in:
- title:    <human-readable title, e.g. "Foo Bar">
- category: <one of wiki/CATEGORIES.md — see file for the authoritative list>
- summary:  <one sentence, ≤25 words — what this stub is anchoring>
- tags:     <comma-separated, 3-8>
- aliases:  <comma-separated, optional — acronyms, alt spellings>

Reply with the five fields filled in, or "abort".
```

Wait for user reply. On "abort", stop. Otherwise parse the five fields.

#### H3. Write the page

Resolve target path:
- Default: `wiki/pages/entities/<slug>.md`
- `--type concept`: `wiki/pages/concepts/<slug>.md`

Construct frontmatter:

```yaml
---
title: "<title>"
type: <entity|concept>
category: <category>
summary: "<summary>"
tags: [<tags>]
aliases: [<aliases>]
confidence: 0.4
sources: []
verify: true
stub_origin: hub
last_updated: <YYYY-MM-DD today>
stale: false
---
```

Construct body:

```markdown
# <title>

> ⚠ needs verification — stub: anchoring <N> incoming wikilinks pending a dedicated source.

## What it is

<placeholder paragraph — one sentence from the user's summary, expanded to
2-3 sentences if the user provided extra context. Otherwise just the summary.>

## Referring pages (at stub creation)

- [[<top-referring-page-1-slug>]]
- [[<top-referring-page-2-slug>]]
- [[<top-referring-page-3-slug>]]
- [[<top-referring-page-4-slug>]]
- [[<top-referring-page-5-slug>]]

*(Strip `<page-type>/` prefix and `.md` suffix from the JSON `referring_pages`
output to get the wikilink slug.)*

## Why this stub exists rather than silent absorption

Our schema's >3-sentence substantial-coverage threshold would normally argue for folding incidental mentions into parent pages. That rule breaks down when cumulative incoming wikilinks make the entity a *discovered graph hub* — <N> pointers at a nonexistent page is stronger signal than any single source alone. The stub with `verify: true` + `confidence: 0.4` + this explicit callout is the honest middle: an anchor for the graph, not a factual claim about <title>.

## Open questions

- <one or two open questions the user can fill in later, or omit entirely>
```

Interpolation slots: `<title>`, `<N>` (from inbound count), `<top-referring-page-*-slug>` (from `referring_pages`). Verbatim everything else — the callout phrasing and "Why this stub exists" section heading are HARD contracts that the lint depends on.

Jump to step 4 (Post-write hooks).

---

## Single-source pattern

Invoked with `<slug> --pattern single-source --source <vault-path>`.
Out-of-band variant of the in-band path that `/wiki-ingest`'s takeaway
gate uses. The skill never auto-fills source content — it writes the
stub shell; the body is the user's call.

#### S1. Validate the source path

The `--source <vault-path>` must point to an existing `vault/...` file.
If missing, tell the user and stop. Resolution rule:

- If path starts with `vault/`, treat as project-relative.
- Otherwise, search `vault/**/*.md` for a file whose stem matches.

Capture the canonical `vault/<...>` path.

#### S2. Prompt for page metadata + gap descriptor

Single structured prompt:

```
Creating single-source stub for `<slug>` (one source: <vault-path>).

Fill in:
- title:          <human-readable title>
- category:       <one of wiki/CATEGORIES.md>
- summary:        <one sentence, ≤25 words>
- tags:           <comma-separated, 3-8>
- aliases:        <comma-separated, optional>
- gap descriptor: <one sentence — what about this entity is NOT yet sourced>

Reply with the six fields filled in, or "abort".
```

#### S3. Write the page

Frontmatter:

```yaml
---
title: "<title>"
type: <entity|concept>
category: <category>
summary: "<summary>"
tags: [<tags>]
aliases: [<aliases>]
confidence: 0.5
sources:
  - <vault-path>
verify: true
stub_origin: single-source
last_updated: <YYYY-MM-DD>
stale: false
---
```

Body:

```markdown
# <title>

⚠ needs verification: single-source entity stub. <gap descriptor>.

## What it is

<user fills this in post-create, or leave as a TODO line>
```

Jump to step 4.

---

## Planned pattern

Invoked with `<slug> --pattern planned --reason "<one-liner>"`. Zero
sources, zero inbound pressure — purely a user-declared research target.

#### P1. Validate input

`--reason "<text>"` is required. If absent, tell the user and stop.

#### P2. Prompt for page metadata

Single structured prompt:

```
Creating planned stub for `<slug>` (research target — no sources captured).
Reason: <reason>

Fill in:
- title:    <human-readable title>
- category: <one of wiki/CATEGORIES.md>
- summary:  <one sentence — what this page will eventually cover>
- tags:     <comma-separated, 3-8>
- aliases:  <comma-separated, optional>

Reply with the five fields filled in, or "abort".
```

#### P3. Write the page

Frontmatter:

```yaml
---
title: "<title>"
type: <entity|concept>
category: <category>
summary: "<summary>"
tags: [<tags>]
aliases: [<aliases>]
confidence: 0.3
sources: []
verify: true
stub_origin: planned
last_updated: <YYYY-MM-DD>
stale: false
---
```

Body:

```markdown
# <title>

⚠ planned research target — no sources captured yet. Reason: <reason>.

## What it will cover

<one or two sentences expanded from the summary — the research-scope hint
for whoever ingests the first source>
```

Jump to step 4.

---

## `--list` informational mode

Invoked with no slug, just `--list`. Surfaces the stub-candidate queue.

Run:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/list_unresolved_hubs.py" wiki --list
```

Render the result as a markdown table for the user:

```
Stub candidates (slugs with ≥10 unresolved inbound wikilinks):

| slug | inbound | top referring pages |
|------|--------:|----------------------|
| <slug-1> | <N> | <top-3-referrers> |
| ... | ... | ... |

To create a hub stub: `/wiki-stub <slug>`
To list at a different threshold: `python "${CLAUDE_PLUGIN_ROOT}/scripts/list_unresolved_hubs.py" wiki --list --threshold N`
```

If `count: 0`, tell the user no candidates exceed the threshold and stop.

The `--list` path never creates a page — it's pure surfacing. The user
picks one and re-invokes the skill with a slug.

---

## 4. Post-write hooks (common to all three patterns)

After the page is written:

#### 4a. Regenerate indexes

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/update_index.py" wiki
```

#### 4b. Commit

Stage the new page + the regenerated index files. Compose a commit
message naming the pattern:

```
stub: create <type>/<slug>.md (<pattern> stub, <one-line-context>)
```

Where `<one-line-context>` is:
- `hub`: `anchoring <N> incoming wikilinks`
- `single-source`: `from <vault-filename>`
- `planned`: `research target — <truncated-reason>`

Example messages:
- `stub: create pages/entities/foo.md (hub stub, anchoring 14 incoming wikilinks)`
- `stub: create pages/entities/bar.md (single-source stub, from 2026-04-foo-article.md)`
- `stub: create pages/concepts/baz.md (planned stub, research target — comparison with adjacent technique)`

#### 4c. Append to `log.md`

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" stub --title "<title>" \
    --created "<type>/<slug>.md" \
    --commit <short-hash> \
    --note "<pattern>-pattern: <context>" \
    --wiki wiki
```

#### 4d. Show the user what happened

Surface the final report:

```
Stub written:
- path:     wiki/<type>/<slug>.md
- pattern:  <hub|single-source|planned>
- frontmatter: confidence=<c>, verify=true, stub_origin=<pattern>
- commit:   <hash>

Next steps:
- The page is in the `needs_verification` lint queue.
- When a source lands (via /wiki-capture → /wiki-ingest), the takeaway
  gate will prompt for graduation (v1.1 hook — manual today: re-evaluate
  confidence and the callout when editing this page).
```

---

## Behavior rules

- **Never overwrite an existing page.** If `<slug>.md` exists, abort with a clear message pointing at the existing file.
- **Never write without explicit metadata.** The five-field prompt is non-skippable.
- **Always run the inbound check for the hub pattern** — even if the user is confident. The N is needed for the callout interpolation.
- **The verbatim callout phrasing is a HARD contract.** Don't paraphrase. The `check_drifted_stubs` lint and hybrid-search retrieval both depend on the exact wording — drift here re-creates the codex shape-drift bug the skill exists to prevent.
- **Single commit per stub.** Bundle the new page + regenerated indexes into one commit.

## Failure modes

- `list_unresolved_hubs.py` errors → surface stderr to the user; do not write the page.
- User aborts at the metadata prompt → exit cleanly; no partial files.
- `update_index.py` errors → the page has been written; surface the error so the user can re-run `update_index.py` manually before committing.
- `append_log.py` errors → the page and indexes are committed; the log entry is missing. Surface the error and offer the command for the user to retry.

## Out of scope (v1.1)

- **Graduation hook in `/wiki-ingest`** — when an EDIT touches a `stub_origin:` page with `verify: true`, surface a graduation prompt at the takeaway gate. Deferred.
- **Pattern B template consolidation in `.claude/agents/wiki-ingestor.md`** — point the ingestor's single-source CREATE path at this skill's template. Deferred.
- **Retroactive backfill of `stub_origin:` on the 6 existing stubs (Cursor, Claude-Desktop, Codex, Khan, FastMCP, GAIK)** — forward-only; backfill on touch.
- **Stale-stub detection (`stub_stale:` based on callout age)** — deferred until a stub crosses 90 days unresolved.
