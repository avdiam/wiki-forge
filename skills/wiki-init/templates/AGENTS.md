# LLM Wiki Schema

> **Schema version:** 4 — see [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) for change history. Bump on breaking changes (new required frontmatter field, renamed page type, removed operation, changed slug format, category-structure change, **category-list addition or removal** since the list is now enumerated and lint-enforced). Non-breaking changes (template tweaks, rule clarifications, prose edits) don't bump. [`CATEGORIES.md`](CATEGORIES.md) is a sibling schema file; see [`README.md`](README.md) for the full layout, special files and tooling notes.

This file is the operational schema for writing pages in this wiki. Agents that write pages (the ingestor; the librarian when filing a synthesis) **Read this file directly**. `CLAUDE.md`'s `@AGENTS.md` include is a pointer for tools that look for `CLAUDE.md`; it loads only on demand (nested `CLAUDE.md`), so it is not relied on. Follow the rules and templates here exactly.

Content lives in `pages/sources/`, `pages/concepts/`, `pages/entities/`, `pages/comparisons/`, `pages/syntheses/`.

## Page types

- **Source summary** (`pages/sources/`) — one per vault file. Captures what the source says, without cross-source synthesis. Filename: same slug as the vault file (`2026-04-15-karpathy-llm-wiki.md`). **What counts as a source:** any file in a `vault/` content folder, except paths whose top-level segment under `vault/` starts with `.` or `_` (protocol files, Obsidian internals) and files with `not_a_source: true` in frontmatter.
- **Concept** (`pages/concepts/`) — an idea, pattern, method or theory. Accumulates evidence from multiple sources; most retrieval happens here. Filename: kebab-case (`rag.md`, `prompt-caching.md`).
- **Entity** (`pages/entities/`) — a specific named thing: person, tool, service, organization, product, model family. Filename: kebab-case (`claude-code.md`, `andrej-karpathy.md`).
- **Comparison** (`pages/comparisons/`) — two competing approaches side by side. Filename: `a-vs-b.md` (`rag-vs-llm-wiki.md`). **Create whenever two entities or concepts occupy the same decision space, even if no single source contrasts them.** After adding or editing an entity/concept page, check whether its natural counterpart exists; if it does and no comparison page exists, create one. Also create one when a source explicitly compares, or surfaces competing claims.
- **Synthesis** (`pages/syntheses/`) — a query result worth preserving ("save this answer" during `/wiki-query`, or a `/wiki-summarize` coverage report). Filename: kebab-case topic (`which-agent-framework-for-python.md`).

## Frontmatter (required on every page)

```yaml
---
title: "Human-readable title"
type: source | concept | entity | comparison | synthesis
category: <one primary category — see below>
summary: "One sentence describing what this page is about."
tags: [free-form, lowercase, dash-separated]
aliases: [alternate-names, abbreviations]
confidence: 0.0-1.0
sources:
  - vault/YYYY-MM-DD-slug.md
last_updated: YYYY-MM-DD
stale: false
---
```

### Field meanings

- **title** — shown in the index and Obsidian tabs.
- **type** — one of the 5 page types.
- **category** — one primary category (see Categories).
- **summary** — ONE sentence (≤ ~25 words) that stands alone as a retrieval hint; the index uses it verbatim.
- **tags** — the primary cross-axis retrieval mechanism. 3–8 per page: conceptual domain (`performance`, `security`), related techniques a searcher would type (`kv-cache`, `latency`), and use-case keywords (`production`, `evaluation`).
- **aliases** — alternate names, acronyms, abbreviations, common misspellings. Always include the acronym if one exists, and the kebab-case form if the title has spaces (`"Retrieval-Augmented Generation"` → `[rag, rag-pipeline, retrieval-augmentation]`).
- **confidence** — `1.0` canonical, multiple strong sources · `0.7–0.9` well supported, gaps · `0.5–0.6` preliminary, single source or contested · `< 0.5` speculative — MUST be flagged inline with `⚠ low confidence: <reason>`.
- **sources** — every vault file that contributed. At least one required (stub pages excepted; see Stub pages).
- **last_updated** — ISO date of last edit.
- **stale** — `true` when the page is known to be outdated but not yet rewritten.
- **source_type** — *required on source pages only.* One of `article`, `paper`, `gist`, `blog`, `docs`, `transcript`, `notes`, `other`, copied from the vault file's frontmatter.
- **captured** — *required on source pages only.* ISO date the raw file landed in `vault/`. Copy the vault file's `captured:` or the Web Clipper's `clipped:`; always write it as `captured:`.
- **published** — *optional, source pages only.* ISO date the content was originally published. Omit when not reliably knowable. When both are present, `published` ≤ `captured`.
- **source_hash** — *required on source pages only.* First 16 hex chars of the vault file's SHA-256 (`sha256_short` from `ingest_source.py`'s JSON brief). Drives content-based dedup.
- **status** — *optional, entity pages only.* `active` (default when absent) · `experimental` · `consolidating` · `deprecated` · `discontinued`. Inferred from recency and explicit signals; update when new evidence arrives.
- **verify** — *optional boolean, any page type.* The page has plausible-but-unsourced or single-source claims that deserve corroboration. When set, also add an inline `⚠ needs verification: <why>` callout at the relevant spot. Distinct from `stale: true` (needs rewriting) and `confidence < 0.5` (possibly wrong). Default for single-source standalone pages (see below).
- **stub_origin** — *optional, any page type.* `hub` | `single-source` | `planned`; marks a low-confidence placeholder. Always paired with `verify: true` and a verbatim stub callout. See Stub pages.

### Claim-tag markers (body only; concept / entity / comparison / synthesis pages)

- `*[INFERRED]*` — your synthesis across sources, or a judgment-call inference from one.
- `*[AMBIGUOUS]*` — a specific contested or single-source claim, where page-level `verify: true` would be too coarse.
- **Untagged = EXTRACTED** (directly attested in a cited source). Flag as the exception, never as a default on every sentence. Keep `*[AMBIGUOUS]*` to ≤ 3 per page; more means the page needs corroborating sources.
- Place the marker after the claim's terminal punctuation, separated by one space.
- **Source pages are exempt** (they are source-faithful by definition).

### Claim-ID anchors (optional, same scope)

Append `{#claim-<slug>}` (slug `[a-z0-9-]+`, unique within the page) after the terminal punctuation, after any `*[TAG]*` marker. Add one **by judgment** only, to a claim likely to be traced, cited across pages, or caught in a contradiction cascade. It makes the claim addressable as `[[page-slug#claim-<slug>]]` for `/wiki-trace`.

> Mark Chen extends this to two-domain wikis. *[INFERRED]* {#claim-chen-two-domain}

**When you anchor an at-risk claim** (page `verify: true`, or the claim tagged INFERRED/AMBIGUOUS) on a multi-source page, attach its supporting quote ending `— [[source-slug]]` so the lint cascade can attribute it to a source.

### Typed cross-reference fields (optional, any page type)

- **contradicts** — pages this page contradicts. Symmetric: if A lists B, B must list A.
- **supersedes** — pages this page replaces (a tool / model / version succeeded by this one).
- **superseded_by** — inverse of `supersedes`; pairs with `status: deprecated` on the superseded page.

Use quoted-flow strings, because bare `[[…]]` parses as a nested YAML list: `contradicts: ["[[slug-a]]", "[[slug-b]]"]`.

```yaml
title: GPT-3
type: entity
status: deprecated
superseded_by: ["[[gpt-4]]"]
```

**Ingestor obligation.** When writing a `> [!WARNING] Contradiction Detected:` callout, also list the referenced pages in this page's `contradicts:` **and** add the inverse edge on each referenced page. For supersession, set `supersedes:` on the new page and `superseded_by:` + `status: deprecated` on the old one. Every wikilink in these fields must resolve to an existing page.

## Categories

The authoritative list and the procedure for adding a category live in [`CATEGORIES.md`](CATEGORIES.md). `category:` must be one of those values (lint rejects anything else). **Never invent a category**; follow `CATEGORIES.md` § "Adding a new category".

## Creating new pages vs editing existing (the most important rule)

When processing a source, for each concept/entity you identify:

1. **Run a fast context query** for existing matches: `grep -ril` across `pages/sources/`, `pages/concepts/`, `pages/entities/`, `pages/comparisons/`, `pages/syntheses/`. Do not load the full index.
2. **Glob `<type>/`** (e.g., `pages/concepts/`) to catch anything grep missed.
3. **If a page exists → EDIT IT.**
   - Append new evidence under `## Evidence` as a `### From <source title>` subsection
   - Add the new source to `sources:`
   - Update `last_updated`
   - Adjust `confidence` (usually upward: more sources, more confidence)
   - If the new source contradicts existing content, add `> [!WARNING] Contradiction Detected: The older text claims [X], but the new source claims [Y].` NEVER silently overwrite.
4. **If no page exists → create one ONLY IF:**
   - The source has substantial coverage (>3 sentences of unique info)
   - You expect future sources to expand it
   - Otherwise: fold the information into the most relevant existing page with a note

**Expect an ingest to edit many pages and create few.** If an ingest creates 10 pages and edits 0, you probably missed connections; re-check with grep.

### Verbatim citation for new entity pages

A new `type: entity` page (person, tool, service, organization, product, model, API, skill, method, version) must include at least one **verbatim quote** from a source that contains the entity name, as a block quote with wikilink attribution:

```markdown
> "Agent Studio is Google's low-code visual authoring environment for AI
> agents on Google Cloud" — [[2026-04-30-notebook-demo-agent-...]]
```

Typical home: just below the `# <Entity Name>` heading or inside `## What it is`. It must be verbatim, not paraphrased. **If no verbatim phrase containing the entity name exists in the source, do not create the entity page**: fold it into existing prose or a related concept/comparison page. Applies to new entity pages only; for concepts, comparisons and syntheses a verbatim quote is encouraged, not required.

### Single-source standalone pages

A new concept/entity/comparison page with exactly one source (`len(sources) == 1`) must satisfy at least one of:

- `confidence: ≤ 0.6`, **OR**
- `verify: true` + an inline `⚠ needs verification: <why>` callout, **OR**
- **explicit user approval at the `/wiki-ingest` takeaway gate**: the gate's `### Proposed operations` annotates the CREATE (e.g. `CREATE pages/concepts/foo.md — single source; recommend verify: true`) and the user approves standalone status.

Otherwise fold the information into the most relevant existing page and wait for a second source.

### Stub pages

`/wiki-stub` owns three stub patterns, all marked with `stub_origin:` + `verify: true` + a verbatim callout: **`hub`** (a slug with ≥10 incoming wikilinks but no source; `sources: []`, confidence 0.4), **`single-source`** (confidence 0.5) and **`planned`** (a user-declared research target; `sources: []`, confidence 0.3). The hub and planned callouts, field values and candidate listing are in the `wiki-stub` skill.

The ingestor creates **single-source stubs** in-band (takeaway-gate single-source CREATE path): `confidence: 0.5`, `verify: true`, `stub_origin: single-source`, and this verbatim callout as body lines 1–2 after the H1 (the exact wording is a HARD contract that lint matches):

```markdown
# <Entity Name>

⚠ needs verification: single-source entity stub. <one-sentence gap descriptor — what's missing beyond the cited source>.
```

**Oversized concept hubs** (approaching ~25K words) are decomposed by extracting evidence clusters, not compressing prose. That procedure lives in the `wiki-scale-audit` skill (`references/hub-decomposition.md`).

## Cross-references

Use Obsidian `[[wikilinks]]` for every internal reference. Targets resolve by slug, not path: `[[rag]]` → `pages/concepts/rag.md`.

- **Validate before writing.** Run `${CLAUDE_PLUGIN_ROOT}/scripts/check_wikilinks.py` on the slugs you plan to link (pass slugs created in the same ingest via `--planned`). It returns `ok | planned | broken` per slug. **Never write a `[[slug]]` the helper marks `broken`**: render it as plain text instead.
- **Link selectively.** Link only where understanding A genuinely changes how you see B; linking every plausible adjacency saturates hubs and turns the graph into noise.

## Core rules (always follow)

1. **Never modify `vault/`.** Vault files are immutable. Corrections go in wiki pages.
2. **Every claim cites a source.** Any factual statement in a wiki page must
   be traceable to a file in `vault/` via the `sources:` frontmatter.
3. **Never silently overwrite contradictions.** New sources contradicting
   existing pages get a `> [!WARNING] Contradiction Detected:` callout.
4. **Flag low-confidence claims inline.** Below 0.5 → `⚠ low confidence: <why>`.
5. **Update indices and `log.md` on every operation.** Non-negotiable. Both are
   script-generated (`update_index.py`, `append_log.py`); never hand-edit.
6. **Commit after every operation.** One commit per ingest/capture/query-filed/
   lint, with a descriptive message.
7. **Human in the loop on ingest.** Present 3–5 key takeaways and wait for
   emphasis/skip feedback before touching pages.
8. **Prefer edits over creates.** See "Creating new pages vs editing existing".
9. **>5 contradictions hard stop.** If processing a source would contradict
   more than 5 existing pages, STOP before touching anything. Surface the
   conflict list to the user and ask whether this is paradigm-shifting
   material that deserves discussion before being absorbed. Do not try to
   reconcile silently.
10. **Temporal facts are not contradictions.** A changed number, version or
    count is a dated snapshot: keep both values with their dates
    (`[2026-04] 12K stars → [2026-09] 30K stars`) instead of a contradiction callout.
11. **Listicles and surveys:** write the source page and the taxonomy concepts
    they organize; don't spawn thin entity stubs for every item listed.

## Writing preferences

House style for every page an agent writes or edits: ingest, query filing,
lint fixes, overview. It applies to new and edited text; don't restyle
untouched passages. A new wiki starts with these defaults. Edit them here,
never in a user's `CLAUDE.md`: page-writing agents run without it.

- **Language:** English, US spelling (`summarize`, `behavior`).
- **Tone:** neutral and encyclopedic. Attribute opinions to their source
  ("Hightower argues…"), never adopt them in the wiki's own voice.
- **No first person.** Pages never say "I" or "we".
- **Terse:** lead with the claim; no filler, no throat-clearing intros, no
  closing recaps.
- **Dates:** `YYYY-MM-DD` in prose as well as frontmatter ("shipped
  2026-04-12", not "April 12th").
- **Numbers:** exact figures with units and a date where they can change
  (`12K stars (2026-04)`), not hedged words ("about twelve thousand").
- **Punctuation:** em dashes are allowed.

## Templates

### Source summary template

```markdown
---
title: "<source title>"
type: source
category: <category>
summary: "<one-sentence description of what this source is and its main claim>"
tags: []
aliases: []
confidence: 0.9
sources:
  - vault/<filename>.md
source_type: <article | paper | gist | blog | docs | transcript | notes | other>
captured: <YYYY-MM-DD>
published: <YYYY-MM-DD>   # optional — omit if not reliably knowable
source_hash: <16-hex sha256_short from ingest_source.py's JSON brief>
last_updated: <YYYY-MM-DD>
stale: false
---

# <source title>

**Author:** <if known>
**Date:** <publication date>
**URL:** <if applicable>
**Captured:** <date>

## Summary
<3-5 sentences describing the source's main argument or content>

## Key claims
- Claim 1 _(confidence: high/medium/low)_
- Claim 2
- Claim 3

## Relevance
<Why this source matters. What gaps does it fill?>

## Pages updated
- [[<page-1>]] — <what was added>
- [[<page-2>]] — <what was added>
```

### Concept page template

```markdown
---
title: "<Concept Name>"
type: concept
category: <category>
summary: "<one-sentence definition of the concept>"
tags: []
aliases: []
confidence: 0.7
sources:
  - vault/<source>.md
last_updated: <YYYY-MM-DD>
stale: false
---

# <Concept Name>

## Definition
<2-3 sentence definition compiled from all sources>

## Evidence

### From <source 1 title>
<What this source contributes>

### From <source 2 title>
<What this source contributes — note agreements or disagreements>

## Contradictions
<If sources disagree, the ingestor adds callouts here:>
> [!WARNING] Contradiction Detected: <source A> claims [X], but <source B> claims [Y].
<Omit this section entirely if no contradictions exist.>

## Open questions
- <Question not yet answered>
- <Question where sources disagree>

## Contrasts with
- [[competing-concept]] — <how they differ; omit section if nothing to contrast>

## Used in
- [[entity-or-system-1]] — <where this concept shows up in practice>
- [[entity-or-system-2]]

## Related
- [[related-concept-1]]
- [[related-entity-1]]
```

### Entity page template

```markdown
---
title: "<Entity Name>"
type: entity
category: <category>
summary: "<one-sentence description of who/what this entity is>"
tags: []
aliases: []
confidence: 0.8
sources:
  - vault/<source>.md
status: <active | experimental | consolidating | deprecated | discontinued>
last_updated: <YYYY-MM-DD>
stale: false
---

# <Entity Name>

## What it is
<Brief description>

## Key attributes
<Relevant properties compiled from sources>

## Timeline
- [YYYY-MM-DD] <event or mention> — [[source-page]]

## Relationships
- Related to [[other-entity]] via <relationship>
- Referenced in [[related-concept]]

## Open questions
- <Unresolved aspects>
```

### Comparison page template

```markdown
---
title: "<A> vs <B>"
type: comparison
category: <category>
summary: "<one-sentence framing of what this comparison is about>"
tags: []
aliases: []
confidence: 0.7
sources:
  - vault/<source>.md
last_updated: <YYYY-MM-DD>
stale: false
---

# <A> vs <B>

## Dimensions

| Dimension | [[a]] | [[b]] |
|-----------|-------|-------|
| <criterion 1> | ... | ... |
| <criterion 2> | ... | ... |

## Verdict
<Clear winner on specific dimensions, or "context-dependent">

## Sources
- [[source-page-1]]
- [[source-page-2]]
```

### Synthesis page template

```markdown
---
title: "<Question or topic>"
type: synthesis
category: <category>
summary: "<one-sentence hook that answers the question>"
tags: []
aliases: []
confidence: 0.7
sources:
  - vault/<source>.md
last_updated: <YYYY-MM-DD>
stale: false
---

# <Question or topic>

## Question
<The original query that prompted this synthesis>

## Answer
<The synthesized answer drawing on multiple wiki pages>

## Sources consulted
- [[wiki-page-1]]
- [[wiki-page-2]]

## Confidence
<Overall confidence and caveats>
```

Coverage reports filed by `/wiki-summarize` extend this template with `subtype: coverage-report` and `coverage_anchor_slug:`; the librarian's survey-mode section documents them.
