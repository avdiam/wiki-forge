# LLM Wiki Schema

This file is the operational schema for the wiki in this directory. It is
auto-loaded by Claude Code when working in `wiki/` and is referenced by the
`wiki-*` skills. Follow the rules and templates here exactly.

## Architecture: three layers

1. **vault/** — Immutable source material (articles, papers, notes, transcripts).
   Read-only from the agent's perspective. Humans add files here. Sibling to wiki/.
2. **wiki/** — Agent-maintained wiki pages. The agent creates, updates, and
   cross-references. Humans read; direct edits are allowed but not the primary mode.
3. **This CLAUDE.md + the wiki-* skills** — The schema and operational rules
   that turn the agent into a disciplined maintainer.

## Tooling Compatibility

If your AI platform (Cursor, Windsurf, Gemini, etc.) does not natively support Claude Code's `/skills` commands or subagent dispatch, refer to `WORKFLOWS.md` for step-by-step manual execution instructions. All operations can be performed using standard terminal commands and the helper scripts in `.scripts/`.

## Directory layout

```text
vault/                  # Immutable sources; filename: YYYY-MM-DD-slug.md
wiki/
├── CLAUDE.md           # This file (auto-loaded by Claude Code in this folder)
├── log.md              # Append-only chronological operation log
├── overview.md         # Evolving big-picture synthesis (rewritten every 5 ingests)
├── .meta/              # Sidecar data (index stats, embedding cache)
├── .scripts/           # Helper scripts (update_index, lint, search, etc.)
│   └── models/         # ONNX model for semantic search (optional)
├── indexes/
│   ├── index.md        # Master index — TOC pointing to sub-indices
│   ├── index-sources.md
│   ├── index-concepts.md
│   ├── index-entities.md
│   ├── index-comparisons.md
│   └── index-syntheses.md
├── sources/            # One summary per vault file
├── concepts/           # Ideas, patterns, methods, theories
├── entities/           # People, tools, services, orgs, models, products
├── comparisons/        # Competing approaches side-by-side
└── syntheses/          # Query answers worth preserving
```

## Page types

### Source summary (`sources/`)

One per vault file. Captures what the source says, without cross-source synthesis.

- Filename: same slug as the vault file
- Example: `sources/2026-04-15-karpathy-llm-wiki.md`

### Concept (`concepts/`)

An idea, pattern, method, or theory. Accumulates evidence from multiple sources
over time. This is where most retrieval happens.

- Filename: kebab-case of the concept name
- Examples: `rag.md`, `llm-wiki-pattern.md`, `prompt-caching.md`

### Entity (`entities/`)

A specific named thing: a person, tool, service, organization, product, or
model family.

- Filename: kebab-case
- Examples: `claude-code.md`, `andrej-karpathy.md`, `langchain.md`, `claude-4-6.md`

### Comparison (`comparisons/`)

Two competing approaches side-by-side. Created when a source explicitly
compares, or when the agent detects competing claims while processing sources.

- Filename: `a-vs-b.md`
- Example: `rag-vs-llm-wiki.md`

### Synthesis (`syntheses/`)

Created from a query result worth preserving. Output of "save this answer as
a wiki page" during `/wiki-query`.

- Filename: kebab-case topic
- Example: `which-agent-framework-for-python.md`

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

- **title** — What appears in the index and in Obsidian tabs.
- **type** — One of the 5 page types above.
- **category** — A single primary category bucket (see Categories below).
- **summary** — ONE sentence (max ~25 words). This is what appears next to
  the page in the sub-indices, so it must stand alone as a retrieval hint.
  The `update_index.py` script pulls this field directly — if it's missing
  or multi-line, the index entry degrades to just the slug.
- **tags** — Multiple allowed. Tags are **the primary cross-axis retrieval
  mechanism** — they appear in the sub-index entries and are matched by both
  keyword search (BM25) and the LLM when scanning indices. Be generous:
  - Include the **conceptual domain** (e.g., `performance`, `cost-optimization`,
    `security`, `architecture`)
  - Include **related techniques** that a searcher might use as query terms
    (e.g., a page on `prompt-caching` should have tags like `inference`,
    `kv-cache`, `context-window`, `latency`, `cost-reduction`)
  - Include **use-case keywords** (e.g., `production`, `prototyping`, `evaluation`)
  - Aim for 3–8 tags per page. More is fine if genuinely relevant; fewer means
    the page is harder to discover.
- **aliases** — Alternate names, acronyms, abbreviations, and common
  misspellings. These appear in the sub-index and let searches find the page
  even when the query uses different vocabulary than the title. Examples:
  - "Retrieval-Augmented Generation" → `aliases: [rag, rag-pipeline, retrieval-augmentation]`
  - "Claude Code" → `aliases: [cc, claude-cli, claude-agent]`
  - "Transformer" → `aliases: [attention-model, self-attention-architecture]`
  - Always include the acronym if one exists.
  - Always include the kebab-case version if the title uses spaces.
- **confidence** — How well-sourced and settled the content is:
  - `1.0`: canonical, widely accepted, multiple strong sources
  - `0.7–0.9`: well-supported but has gaps
  - `0.5–0.6`: preliminary, single source, or contested
  - `< 0.5`: speculative — MUST be flagged inline with `⚠ low confidence: <reason>`
- **sources** — Every vault file that contributed. At least one required.
- **last_updated** — ISO date of last edit.
- **stale** — `true` when the page is known to be outdated but not yet rewritten.
  The lint operation uses this.

## Categories

Categories are a **flexible** taxonomy. The wiki starts with a seed set, but
**new categories can be created** when material genuinely doesn't fit.

### Seed categories

- `claude-code` — Claude Code features, skills, hooks, MCP servers, CLI
- `agentic-frameworks` — LangChain, LlamaIndex, AutoGen, LangGraph, CrewAI, etc.
- `agent-platforms` — Vertex AI Agent Builder, AgentHub, ADK, hosted platforms
- `llm-concepts` — RAG, fine-tuning, prompting, memory, eval, architectures
- `models` — Specific model families (Claude 4.6, GPT-5, Gemini, etc.)
- `languages` — Python, TypeScript, Rust (only for language-specific content)

### Creating new categories

When a source introduces material that doesn't fit any existing category:

1. **Check if an existing category can be broadened.** If `claude-code` is now
   sharing space with notes on Cursor and Aider, propose renaming it to
   `coding-agents`. Breadth > proliferation.
2. **If a new category is warranted**, propose it to the user BEFORE creating
   pages under it. State:
   - The proposed category name (kebab-case)
   - Why existing categories don't fit
   - Which pages would go in it
3. **Never create a category with a single page.** Either fold into an existing
   category or wait for a second source.

## Creating new pages vs editing existing (the most important rule)

When processing a source, for each concept/entity you identify:

1. **Run a fast context query** for existing matches. Use `grep -ril` across
   `sources/`, `concepts/`, `entities/`, `comparisons/`, `syntheses/` for fast verification. Do not load the full index.
2. **Glob `<type>/`** (e.g., `concepts/`) to catch anything grep might have missed.
3. **If a page exists → EDIT IT.**
   - Append new evidence under `## Evidence` with a `### From <source title>` subsection
   - Add the new source to the `sources:` list in frontmatter
   - Update `last_updated`
   - Adjust `confidence` (usually upward — more sources = more confidence)
   - If the new source contradicts existing content, add an Obsidian callout:
     `> [!WARNING] Contradiction Detected: The older text claims [X], but the new source claims [Y].`
     NEVER silently overwrite.
4. **If no page exists → create one ONLY IF:**
   - The source has substantial coverage (>3 sentences of unique info)
   - You expect future sources to expand it
   - Otherwise: fold the information into the most relevant existing page with a note

**Expect ingest to edit many pages and create few.** Karpathy's own wikis
touch 10–15 existing pages per new source. If your ingest creates 10 pages
and edits 0, you probably missed connections — re-check via grep tools.

## Cross-references

Use Obsidian-style `[[wikilinks]]` for every internal reference. Targets
resolve by slug, not full path:

- `[[rag]]` → `concepts/rag.md`
- `[[karpathy]]` → `entities/karpathy.md`
- `[[rag-vs-llm-wiki]]` → `comparisons/rag-vs-llm-wiki.md`

Broken wikilinks (pointing to non-existent pages) are flagged by `wiki-lint`.

## Special files

### `indexes/index.md` (master index)

The navigation spine. Acts as a master directory pointing to sub-indices
for each type (`indexes/index-concepts.md`, `indexes/index-entities.md`, etc.).

Within each sub-index, organized by category, every page appears as one line
with its aliases, tags, and metadata — all visible to both the LLM and search:

```text
- [[slug]] — one-line description `[aka: alt-name, acronym | tags: topic, domain, technique]` _(confidence: 0.8, sources: 3)_
```

The `aka:` and `tags:` fields in the index entry are what make pages
discoverable by alternate vocabulary. A query for "kv-cache" finds a page
titled "Prompt Caching" because the tag appears in the index line.

Updated on every operation via `update_index.py`. Never let it drift.

### `log.md`

Append-only. Each operation adds an entry:

```markdown
## [2026-04-15 14:30] ingest | Karpathy LLM Wiki gist
- Created: sources/2026-04-15-karpathy-llm-wiki.md
- Created: concepts/llm-wiki-pattern.md
- Updated: concepts/rag.md (added contradiction section)
- Updated: entities/karpathy.md (source added, confidence 0.8→0.9)
- Commit: a1b2c3d
```

### `overview.md`

A single page of prose describing the current state of the wiki — major
themes, recent additions, unresolved questions, interesting connections.
Rewritten (not just appended) every 5th ingest, or on explicit request.

### Semantic search (optional)

If `.scripts/hybrid_search.py` and `.scripts/models/` are present, the
wiki-query skill can use hybrid BM25+semantic search for page discovery.
This is only used for query answering, never for ingestion (the ingestor
uses grep for speed and zero-dependency operation).

The search is conditional: for small wikis (under ~4000 index tokens),
the query skill reads the sub-indices directly. Semantic search activates
automatically as the wiki grows.

To set up: `python .scripts/setup_search.py`
To disable: delete `.scripts/models/`

### Graph analysis

`.scripts/graph_analyzer.py` analyzes the wikilink topology — hubs (most-
linked pages), sinks (dead ends), orphans, and connected components. The
wiki-linter runs this during Pass A to detect structural problems that
page-level checks miss (e.g., isolated clusters of pages that should be
cross-referenced).

### Multi-tool compatibility

This schema is also available as `AGENTS.md` for tools that use that
filename (Codex CLI, Cursor, Antigravity, Gemini CLI, OpenCode). Both
files contain identical content. Edit either; keep the other in sync.

## Core rules (always follow)

1. **Never modify `vault/`.** Vault files are immutable. Corrections go in wiki pages.
2. **Every claim cites a source.** Any factual statement in a wiki page must
   be traceable to a file in `vault/` via the `sources:` frontmatter.
3. **Never silently overwrite contradictions.** New sources contradicting
   existing pages get a `> [!WARNING] Contradiction Detected:` callout.
4. **Flag low-confidence claims inline.** Below 0.5 → `⚠ low confidence: <why>`.
5. **Update indices and `log.md` on every operation.** Non-negotiable.
6. **Commit after every operation.** One commit per ingest/capture/query-filed/
   lint, with a descriptive message.
7. **Human in the loop on ingest.** Present 3–5 key takeaways and wait for
   emphasis/skip feedback before touching pages.
8. **Prefer edits over creates.** See "Creating new pages vs editing existing".
9. **>5 contradictions hard stop.** If processing a source would contradict
   more than 5 existing pages, STOP before touching anything. Surface the
   conflict list to the user and ask whether this is paradigm-shifting
   material that deserves discussion before being absorbed. Do not try to
   reconcile silently — that's how wikis quietly corrupt themselves.

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
