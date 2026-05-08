# LLM Wiki Schema

> **Schema version:** 2 — see [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) for change history. Bump on breaking changes (new required frontmatter field, renamed page type, removed operation, changed slug format, category-structure change, **category-list addition or removal** since the list is now enumerated and lint-enforced). Non-breaking changes (template tweaks, rule clarifications, prose edits) don't bump. Both `CLAUDE.md` and `AGENTS.md` must carry the same version — `lint_wiki.py`'s `schema_sync` check enforces byte-identical content.

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

Two competing approaches side-by-side. **Create whenever two entities or
concepts occupy the same decision space, even if no single source contrasts
them directly.** After adding or editing an entity/concept page, check
whether its natural counterpart already exists — if yes and there isn't
already a comparison page, create one. Common triggers:

- Two agent frameworks (LangChain vs CrewAI vs AutoGen)
- Two vector databases (Pinecone vs pgvector vs sqlite-vec)
- Two retrieval strategies (RAG vs LLM wiki vs context-stuffing)
- Two model families serving the same need

Also created when a source explicitly compares, or when processing a source
surfaces competing claims.

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
- **source_type** — *Required on `sources/` pages; not used elsewhere.* The
  raw-material category — one of `article`, `paper`, `gist`, `blog`, `docs`,
  `transcript`, `notes`, `other`. Copied verbatim from the raw vault file's
  frontmatter (which the `wiki-capture` skill writes on capture). Helps
  calibrate trust — a claim from a peer-reviewed paper carries different
  weight than the same claim from a blog post, even at identical confidence.
- **captured** — *Required on `sources/` pages; not used elsewhere.* ISO date
  the raw source material was pulled into `vault/`. Copied from the raw vault
  file's frontmatter (`captured:` written by `/wiki-capture`, or `clipped:`
  written by the clipper — ingestor accepts either and writes `captured:` on
  the source page). Distinct from `last_updated` (the wiki-page edit date).
  Drives the `stale_source` lint check — source pages whose `captured:` is
  older than 180 days surface as a review queue, signaling upstream content
  may have drifted since the original capture (see [[llm-wiki-failure-modes]]
  #8 on dynamic-data mismatch).
- **published** — *Optional on `sources/` pages; not used elsewhere.* ISO date
  the underlying content was originally published in the world. Distinct from
  `captured:` (when the raw source landed in `vault/`) — the two are
  deliberately separate. Capture date is always knowable; publication date
  often isn't (gists, undated posts, preprint-vs-final, post-publication
  edits). Omit when not reliably knowable — its absence is not a lint event.
  When present and meaningful (article with a stated publish date, paper with
  a submission date), record it; this preserves provenance and lets the
  schema represent the temporal split between "when written" and "when
  ingested." `captured:` anchors the filename and the `stale_source` lint;
  `published:` is purely informational. Invariant: when both are present,
  `published:` should be ≤ `captured:` (a future lint may surface
  violations).
- **source_hash** — *Required on `sources/` pages; not used elsewhere.* First
  16 hex characters of the raw vault file's SHA-256 (`sha256_short` from
  `ingest_source.py`'s JSON brief). 64 bits is far past birthday-bound
  collision risk for any personal knowledge base — the short form is both
  inspectable in frontmatter and sufficient for dedup. Drives content-based
  dedup: before every ingest, the `/wiki-ingest` skill greps `sources/*.md`
  frontmatter for a matching `source_hash:` and prompts the user on match.
  Robust to filename / URL / date-slug variations that slug-based and
  URL-based dedup miss.
- **status** — *Optional on `entities/` pages; not used elsewhere.* Tool /
  framework / model / product lifecycle state:
  - `active` — in use, maintained, worth recommending (default when absent)
  - `experimental` — early/beta; shape likely to change
  - `consolidating` — mature but slowing; still viable
  - `deprecated` — officially superseded; avoid for new work
  - `discontinued` — abandoned or shut down

  Inferred from source recency and explicit signal (deprecation notices,
  acquisition announcements, "last released N years ago"). Not a hard
  commitment — update when new evidence arrives.
- **verify** — *Optional, boolean, any page type.* Flag for claims that are
  plausible but unsourced, or single-source assertions that deserve
  independent corroboration. Softer than `stale: true` (which means "this
  wiki page needs rewriting") and different from `confidence < 0.5` (which
  means "we think this might be wrong outright"). When set, also add an
  inline `⚠ needs verification: <why>` callout at the relevant spot. Lint
  surfaces `verify: true` pages as a review queue.

  **Canonical use case: single-source standalone pages.** A new
  concept/entity/comparison page created from `len(sources) == 1` defaults
  to `verify: true` unless the user explicitly approved standalone status
  at the `/wiki-ingest` takeaway gate. The `single_source_unverified`
  lint check surfaces single-source pages with `confidence > 0.6` and no
  `verify: true` as a soft-warning review queue — see "Creating new pages
  vs editing existing" below for the full rule.

#### Typed cross-reference fields (optional, any page type)

These three fields surface structured cross-page relationships in
frontmatter, complementing the inline `> [!WARNING]` callout shapes used in
page bodies. They are optional — absence is never a lint event — but when
present they unlock graph-walk queries, asymmetry checks, and downstream
typed-edge tooling.

- **contradicts** — *Optional, list of wikilinks; typically concepts /
  comparisons / syntheses / sources.* Pages this page contradicts. The
  inline `> [!WARNING] Contradiction Detected:` callout in page body
  remains the prose anchor; this field is the structured surface of the
  same fact so `graph_analyzer.py` can count contradiction edges and
  `lint_wiki.py` can enforce asymmetry. Symmetric invariant: page A's
  `contradicts:` listing B implies B's `contradicts:` lists A — the
  `check_asymmetric_contradictions` lint flags one-way edges.
- **supersedes** — *Optional, list of wikilinks; typically entities.*
  Pages this page supersedes — used when one tool / model / framework /
  version replaces a previous one.
- **superseded_by** — *Optional, list of wikilinks; typically entities.*
  Inverse of `supersedes:`. Pairs with `status: deprecated` on the
  superseded page. Lets queries route from a historical page to its
  current canonical replacement.

**Canonical YAML shape:** quoted-flow strings —
`contradicts: ["[[slug-a]]", "[[slug-b]]"]`. Bare `[[…]]` parses as a
nested YAML list, not a wikilink string; quoting forces string semantics.
Matches the existing `tags: [...]` / `aliases: [...]` flow style except the
wikilink contents force quoting.

Example — entity page deprecated in favor of a successor:

```yaml
title: GPT-3
type: entity
status: deprecated
superseded_by: ["[[gpt-4]]"]
```

Example — comparison page that takes a position contradicted elsewhere:

```yaml
title: "Spec-driven development: waterfall or not?"
type: comparison
contradicts: ["[[spec-driven-development-as-iteration]]"]
```

**Ingestor obligation (active).** When writing a `> [!WARNING]
Contradiction Detected:` callout, also populate this page's `contradicts:`
field with the wikilinks referenced in the callout, AND add the inverse
edge to each referenced page's `contradicts:` field so the relationship is
symmetric. Same pattern for supersession: populate `supersedes:` on the
new page and `superseded_by:` (with `status: deprecated`) on the old page.
The `check_invalid_typed_links` lint flags any wikilink in these fields
that doesn't resolve to an existing page.

## Categories

Categories are an **enumerated, lint-enforced taxonomy**. Every page's
`category:` frontmatter must be one of the values listed below. Adding a new
category requires editing this file (both `CLAUDE.md` and `AGENTS.md`) and
bumping the schema version — it is not a per-page decision the agent can
make autonomously.

`lint_wiki.py`'s `unknown_category` check rejects any page whose `category:`
is outside this list with a hard error.

### Authoritative category list

- `coding-agents` — Autonomous coding tools, agent loops, IDE integrations, code generation
- `agentic-sdks` — Frameworks for building agents (LangChain, CrewAI, AutoGen, LangGraph, etc.)
- `tool-use-and-skills` — Function calling, MCP, skill systems, tool integration
- `ai-practitioners` — People (researchers, developers, writers, founders) whose work is cited or discussed. One page per person regardless of topical focus; slug uses `first-last` full name without honorifics or credentials. Type-axis category — co-locating all people avoids brittle re-shuffling as their coverage broadens.
- `llm-architectures` — Model design, scaling laws, attention mechanisms, transformer variants
- `personal-knowledge-management` — LLM Wiki pattern, second brains, note-taking systems for AI-augmented knowledge work, the wiki's own founding cluster (Karpathy gist + descendants).
- `ai-product-strategy` — Product positioning, market dynamics, demo-vs-real-product tensions, go-to-market patterns specific to AI-first products. Distinct from `coding-agents` (the tools themselves) — this category is about how those tools land commercially.
- `ai-infrastructure` — Platforms, hubs, registries, and distribution infrastructure that other AI work runs on (model/dataset hubs like Hugging Face; hosted inference like Modal, Replicate, Together AI, Anyscale; runtime stacks like vLLM-as-service). Distinct from `ai-labs` (model-builders that produce the artifacts these platforms host) and from `coding-agents` / `agentic-sdks` (consumer-side tools that *use* these platforms). When a page sits at the boundary, prefer the layer the source page actually discusses.
- `ai-research-frontiers` — Active research directions and theoretical advances (JEPA, world models, geospatial embeddings, novel architectures pre-productization). Distinct from `llm-architectures` which is settled-and-shipped transformer-family work; this category is for material that's still in the lab or recently emerged from it.
- `ai-labs` — Model-builder organizations that produce foundation artifacts (Anthropic, OpenAI, Google DeepMind, Mistral, etc.). Distinct from `ai-infrastructure` (the platforms that host or serve those artifacts) and from `ai-practitioners` (individuals, not orgs).
- `training-and-fine-tuning` — RLHF, SFT, distillation, evals, dataset curation
- `prompt-engineering` — Prompting techniques, patterns, system prompts, chain-of-thought
- `languages-and-frameworks` — Python, TypeScript, Rust, and language-specific libraries

### Adding a new category

When a source introduces material that doesn't fit any existing category, the
agent **does not silently create a new category**. The flow is:

1. **First, check if an existing category can absorb the page.** If `coding-agents`
   is now sharing space with notes on general AI assistants, propose renaming
   it. Breadth > proliferation.
2. **If a new category is genuinely needed**, surface this to the user at the
   `/wiki-ingest` takeaway approval gate (step 11) under `### New categories
   proposed`. State:
   - The proposed category name (kebab-case)
   - Why existing categories don't fit
   - Which pages would go in it (must be ≥2 — never create a single-page category)
3. **The user must approve, and the schema must be edited.** When approved, the
   user (or the agent at the user's direction) edits this file AND `AGENTS.md`
   AND `SCHEMA_CHANGELOG.md`, bumping `Schema version`. Only after the schema
   bump can the agent create the new pages.
4. **Never create a category with a single page.** Either fold into an existing
   category or wait for a second source — the schema bump is itself gated on
   ≥2 pages worth of material.

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

### Verbatim citation for new entity pages

When creating a new `type: entity` page (a specific person, tool, service,
organization, product, model, API, skill, method, or version), the page
must include at least one **verbatim quote** from a source that contains
the entity name. Format as a Markdown block quote with wikilink attribution:

```markdown
> "Agent Studio is Google's low-code visual authoring environment for AI
> agents on Google Cloud" — [[2026-04-30-notebook-demo-agent-...]]
```

The quote can live anywhere in the body — typical home is just below the
`# <Entity Name>` heading or inside `## What it is` — but it must be
verbatim from the source, not paraphrased.

**Why this rule exists.** The most acute failure mode for entity creation
is the agent inventing a specific name (API, skill, method) that doesn't
appear in any source — fabrication. By forcing a verbatim citation, the
agent has to point at the exact phrase that backs the name, and the
`/wiki-ingest` takeaway approval gate (step 11) surfaces this quote so
the user can catch fabrications **before** the page is written.

If the agent cannot find a verbatim phrase containing the entity name in
the source, the entity is folded into existing prose (or a related
concept/comparison page) rather than promoted to a standalone entity page.

**Forward-only rule.** Existing entity pages predating this rule are not
required to add verbatim citations retroactively. The rule applies to new
entity creation going forward. Concepts, comparisons, and syntheses are
not subject to this rule — verbatim citation is encouraged but not
required, since those page types aggregate across sources where no single
quote may capture the synthesis.

### Single-source standalone pages

When a new concept/entity/comparison page would have exactly one source
(`len(sources) == 1`), the agent must satisfy at least one of:

- `confidence: ≤ 0.6` — explicit acknowledgment that a single source is
  preliminary evidence, **OR**
- `verify: true` + an inline `⚠ needs verification: <why>` callout — page
  enters the review queue and lint surfaces it for corroboration, **OR**
- **Explicit user approval at the `/wiki-ingest` takeaway gate.** The
  takeaway block's `### Proposed operations` must annotate single-source
  CREATE entries (e.g., `CREATE concepts/foo.md — single source; recommend
  verify: true`) and the user's approval reply ("yes promote — this is
  canonical / single-source ok") clears the requirement.

Otherwise: fold the information into the most relevant existing page with
a note, and wait for a second source before promoting.

The `single_source_unverified` lint check surfaces violations as a
soft-warning review queue. Existing pages predating this rule are part of
that queue and can be cleaned up incrementally — the rule is not
retroactive-blocking.

**Expect ingest to edit many pages and create few.** Karpathy's own wikis
touch 10–15 existing pages per new source. If your ingest creates 10 pages
and edits 0, you probably missed connections — re-check via grep tools.

## Cross-references

Use Obsidian-style `[[wikilinks]]` for every internal reference. Targets
resolve by slug, not full path:

- `[[rag]]` → `concepts/rag.md`
- `[[andrej-karpathy]]` → `entities/karpathy.md`
- `[[rag-vs-llm-wiki]]` → `comparisons/rag-vs-llm-wiki.md`

Broken wikilinks (pointing to non-existent pages) are flagged by `wiki-lint`
post-write. The wiki-ingestor agent also validates wikilinks **pre-write**
via `wiki/.scripts/check_wikilinks.py` (a stdlib helper that takes a slug
list — bare or `[[bracketed]]` — plus an optional `--planned` set for slugs
being created in the same ingest, and returns `ok | planned | broken` per
slug). The pre-write validation is the load-bearing rule: do **not** write
a `[[slug]]` to a target the helper marks `broken`. Render as plain text
instead and wait for a future ingest to resolve it. This eliminates the
broken-link fix-up commit class that surfaced in Phase B of the
inline-vs-fork experiment.

**Selective-linking discipline.** Keep connections tight: only link where
understanding A genuinely changes how you see B. Constraint improves quality —
a wiki where every plausible adjacency is a wikilink degrades into noise, with
hub pages saturating and the graph view losing its "what's load-bearing here"
signal. Forward-only rule; not a retroactive sweep target. (Per [[evgeni-rusev]];
see [[2026-04-28-second-brain-obsidian-claude-code-evgeni-rusev]].)

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

The search is conditional: for small wikis (under ~6000 index tokens,
roughly ~90 pages at typical entry density), the query skill reads the
sub-indices directly. Semantic search activates automatically as the wiki
grows past that point.

To set up: `python .scripts/setup_search.py`
To disable: delete `.scripts/models/`

### Graph analysis

`.scripts/graph_analyzer.py` analyzes the wikilink topology — hubs (most-
linked pages), sinks (dead ends), orphans, and connected components. The
wiki-linter runs this during Pass A to detect structural problems that
page-level checks miss (e.g., isolated clusters of pages that should be
cross-referenced).

### Lint configuration (optional) — `.meta/lint-config.json`

`lint_wiki.py` reads this sidecar (when present) to gate the
`artifact_presence` check. The check flags pages that lack any structural
artifact — fenced code blocks, GFM tables, numbered procedures (≥2 entries),
attributed block quotes (`> "…"`), or definition lists (`**term** — defn`,
≥2 entries). The patterns are domain-agnostic; what differs across wikis is
which page categories *should* expect them.

Schema: `{"artifact_check_categories": ["cat-a", "cat-b", ...]}`. Empty or
missing allowlist → check is silent (default for fresh wikis). Source-summary
pages are always excluded — they're the raw-citation transit layer, not
synthesized content. Soft-warning severity; never blocking.

Cross-domain rationale: a philosophy or history wiki may legitimately have
pure-prose concept pages where requiring code blocks or tables would be
wrong. The opt-in mechanism keeps the check useful for technical wikis
without forcing IT-flavor conventions on every domain. Per-page override
via a `has_artifact: true | false` frontmatter flag is a deferred follow-on;
revisit if the per-category mechanism shows real false-positive volume.

### Secret-pattern config (optional) — `.meta/secret-patterns.json`

`wiki/.scripts/check_secrets.py` ships with a built-in pattern set
covering the highest-volume credential prefixes — GitHub PATs (classic /
oauth / fine-grained), AWS access keys, Stripe live + test secrets,
OpenAI keys, Anthropic keys, Google API keys, Slack tokens — plus a
soft-warning pass over high-entropy strings (≥40 chars, mixed
letter+digit) inside fenced code blocks. The scanner runs at
`/wiki-ingest` step 10.5 against the source file before the takeaway
gate; HALT semantics on high findings, soft warning on low.

Schema: `{"<pattern_name>": "<python_regex>", ...}` — extends (does not
replace) the built-ins. Each value is compiled at scan time with
`re.compile`; invalid patterns are skipped with a stderr warning rather
than crashing the scan.

Use cases: pinning a custom corporate-domain auth-token format,
suppressing a known false-positive class via more specific replacement
of the built-in (define a same-named entry to override), or adding
emerging credential prefixes from new SaaS integrations. Keep additions
narrow — broad regexes degrade the signal-to-noise of HALT severity.

The scanner is the parser-side application of the [[lethal-trifecta]]
"sanitize the untrusted-content leg" mitigation: categorically reliable
for known patterns, no model dependency. Pure stdlib; no `--json`
toggle (output is always JSON to stdout); exit code 0 / 1 / 2 mirrors
no findings / low only / any high.

### Telemetry (optional)

Five append-only JSONL logs under `.meta/` capture per-operation cost
and retrieval data — one per side-effecting operation (ingest, query,
lint, overview) plus one for source-freshness polling — enabling
empirical validation of the wiki pattern's
ingest-amortizes-through-query-reuse bet (see `[[llm-wiki-failure-modes]]`
section #6). On-demand analysis only — nothing runs automatically.

**`.meta/ingest_log.jsonl`** — one row per ingest. Written by `/wiki-ingest`
after the ingestor subagent returns; cost fields are parsed from the
`<usage>` block the Agent tool surfaces on subagent completion.

```json
{
  "timestamp": "2026-04-20T14:30:05",
  "source_path": "vault/Web Articles/2026-04-15-foo.md",
  "source_type": "article",
  "source_word_count": 3136,
  "pages_created": ["sources/2026-04-15-foo", "concepts/bar"],
  "pages_updated": ["entities/baz"],
  "contradictions_flagged": 0,
  "total_tokens": 158204,
  "input_tokens": 122,
  "cache_creation_input_tokens": 18432,
  "cache_read_input_tokens": 138210,
  "output_tokens": 1440,
  "tool_uses": 47,
  "duration_ms": 312405,
  "commit_hash": "a1b2c3d"
}
```

The four per-field token counts (`input_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`,
`output_tokens`) are surfaced by the Agent tool's `<usage>` block when
present and captured per-field — any subset may be `null` if the block
omits a field. `cache_creation_input_tokens` is the load-bearing slice
for measuring cache savings from auto-loaded context (e.g., the
SessionStart hook); `cache_read_input_tokens` is the cache-hit volume
that scales with replayed conversation context. `total_tokens` remains
the aggregate.

**`.meta/query_log.jsonl`** — one row per `/wiki-query`, `/wiki-graph`,
or `/wiki-summarize` invocation. The `query_skill` field distinguishes
the origins. `/wiki-query` writes on both handling paths: inline (small
wiki, no subagent) and librarian-dispatch (large wiki). `/wiki-graph`
writes one row per traversal (path / neighbors / subgraph) — pure script
output, no subagent, no model call. `/wiki-summarize` always dispatches
the librarian in survey-mode and writes a row structurally similar to
`/wiki-query` Path B.

```json
{
  "timestamp": "2026-04-20T14:31:12",
  "query_skill": "query",
  "query": "what is RAG",
  "query_variations": ["retrieval-augmented generation", "RAG pipeline"],
  "search_mode": "hybrid",
  "pages_consulted": ["concepts/rag", "concepts/llm-wiki-pattern"],
  "top_score": 0.82,
  "filed_as": null,
  "total_tokens": null,
  "input_tokens": null,
  "cache_creation_input_tokens": null,
  "cache_read_input_tokens": null,
  "output_tokens": null,
  "tool_uses": null,
  "duration_ms": 8100
}
```

`query_skill` values: `"query"` (default; `/wiki-query` invocations),
`"graph"` (`/wiki-graph` invocations), or `"summarize"` (`/wiki-summarize`
invocations). Forward-compatible with rows predating the field — the
absence of `query_skill` is treated as `"query"` by `query_stats.py`.

`search_mode` values: `"inline"` (Path A), `"hybrid" | "hybrid_zvec" |
"bm25_only"` (Path B retrieval modes from `hybrid_search.py`),
`"slug-anchored"` (`/wiki-summarize` Shape A — topic resolved to an
existing slug, pages from `graph_query.neighbors_within`), or `null`
(graph rows).

On Path A (`search_mode: inline`) all six cost fields are `null` — no
subagent is dispatched, so no `<usage>` metadata reaches the caller;
`duration_ms` (main-session wall-clock) is the Path-A cost proxy. On
Path B (`search_mode: hybrid | hybrid_zvec | bm25_only`), every cost
field is parsed independently from the librarian's `<usage>` block;
fields the block omits stay `null`.

For `query_skill: "graph"` rows, `search_mode` and all six cost fields
are `null` (no model call). `query` carries the raw subcommand + args
(e.g., `"neighbors lethal-trifecta --depth 2"`); `pages_consulted`
carries the result set (path slugs / neighbor slugs / subgraph nodes);
`duration_ms` is the script wall-clock from `graph_query.py`'s JSON output.

For `query_skill: "summarize"` rows, structure mirrors `query` Path B —
the librarian is always dispatched in survey-mode, so cost fields are
parsed from its `<usage>` block. `search_mode` is `"slug-anchored"` for
Shape A (topic resolves to an existing slug; pages = anchor +
`neighbors_within(depth=1)`) or `"hybrid"` for Shape B (fuzzy fallback;
pages = `hybrid_search.search` top-k=25 results). `query` carries the
raw user topic; `filed_as` records the coverage-report synthesis slug
(`syntheses/<slug>-coverage`) if filed, else `null`. Empty Shape B
results write no row — the skill emits the dual-onramp inline without
dispatching the librarian.

`query_variations` (Phase 2 — multi-query expansion) is the JSON array of
paraphrase variations the `/wiki-query` skill generated and passed to
`hybrid_search.py`'s `--queries-json` flag, *excluding* the canonical
first query (which lives in `query`). `null` on Path A (no expansion),
on N=1 Path B, and on all `query_skill: "graph"` rows.

**`.meta/lint_log.jsonl`** — one row per lint run. Written from two paths:
(a) the post-commit mechanical lint the ingestor runs on every ingest, and
(b) the full three-pass `/wiki-lint` run. The `lint_type` field
distinguishes them.

```json
{
  "timestamp": "2026-04-21T14:32:04",
  "lint_type": "full",
  "ingest_count_at_run": 27,
  "total_pages": 93,
  "counts": {
    "broken_links": 0, "orphans": 2, "missing_frontmatter": 0,
    "stale": 0, "low_confidence": 1, "index_drift": 0,
    "source_backlog": 0, "stale_source": 0,
    "overview_staleness": 0, "single_page_categories": 1,
    "long_summary": 0, "needs_verification": 3,
    "unacknowledged_contradictions": 0, "category_issues": 0
  },
  "verdict": "issues",
  "semantic_pass_ran": true,
  "total_tokens": 48210,
  "input_tokens": 95,
  "cache_creation_input_tokens": 6120,
  "cache_read_input_tokens": 41610,
  "output_tokens": 385,
  "tool_uses": 18,
  "duration_ms": 184500,
  "commit_hash": "a1b2c3d"
}
```

`lint_type: mechanical` rows have `semantic_pass_ran: false`, all six
cost fields null (stdlib script, no subagent), and typically
`commit_hash: null` (mechanical runs don't commit on their own).
`lint_type: full` rows have `semantic_pass_ran: true` and cost fields
populated per-field from the linter subagent's `<usage>` block; missing
sub-fields stay null. `commit_hash` is populated only if the user
approved fixes that produced a commit. `ingest_count_at_run` is a
monotonic snapshot — the count of ingest entries in `log.md` at the
moment lint fired — and is the anchor for "ingests since last full lint"
derivations (current ingest count minus max `ingest_count_at_run` over
rows with `lint_type: full`).

**`.meta/overview_log.jsonl`** — one row per `/wiki-overview` invocation,
written from Mode A (soft refresh) and Mode B (full rewrite) on curator
return.

```json
{
  "timestamp": "2026-05-07T14:30:21",
  "mode": "soft",
  "trigger": null,
  "ingest_count_at_run": 120,
  "status": "applied",
  "total_tokens": 28100,
  "input_tokens": 60,
  "cache_creation_input_tokens": 4200,
  "cache_read_input_tokens": 22300,
  "output_tokens": 540,
  "tool_uses": 9,
  "duration_ms": 41200,
  "commit_hash": "a1b2c3d"
}
```

`mode: soft` rows carry `trigger: null` (soft refresh has no trigger —
it's the cadence-driven bookkeeping pass). `mode: full` rows carry the
trigger reasons string from `check_overview_triggers.py` or the literal
`"user-invoked"` when the user passed `--full`. `status` is `applied`
when the dispatch produced a commit, `no_changes` when Mode A's diff
came up empty (skip-the-commit path), or `cancelled` when Mode B's
Pass 2 returned `status: cancelled` (user rejected the rewrite at the
approval gate). `commit_hash` is null on `no_changes` / `cancelled`.

Mode B emits two `<usage>` blocks (one per pass). The skill aggregates
across both passes for the row's cost fields and `duration_ms` per the
file-checkpoint HITL contract; `commit_hash` is from the Pass-2 commit
only.

**`.meta/source_freshness.jsonl`** — one row per source-page poll,
written by `wiki/.scripts/check_source_freshness.py` (phase 2 of the
[[llm-wiki-failure-modes]] #8 mitigation). Each poll establishes or
checks the slug's content-drift baseline.

```json
{
  "polled_at": "2026-05-07T14:35:12",
  "slug": "2025-11-25-writing-a-good-claude-md-humanlayer",
  "url": "https://www.humanlayer.dev/blog/writing-a-good-claude-md",
  "http_status": 200,
  "content_hash": "a1b2c3d4e5f6a7b8",
  "baseline_hash": "a1b2c3d4e5f6a7b8",
  "drifted": false,
  "error": null,
  "reason": null
}
```

`http_status` is the upstream HTTP code on a successful fetch (`200`,
`404`, …) OR a string sentinel for skipped / failed polls:

- `"skipped"` with `reason: "requires_clipper"` — paywall domain
  (Medium and known mirrors); phase 3 wires these to clipper.
- `"skipped"` with `reason: "no_url"` or `"no_vault_link"` — source
  page has no resolvable upstream URL; nothing to poll.
- `"error"` with `error: "timeout" | "dns" | "connection" | …` —
  network failure after 1 retry; row is recorded so the failure is
  audit-visible.

`content_hash` is `sha256(extracted_text)[:16]` — same 16-hex shape as
`source_hash:` for visual parity but hashing different bytes (extracted
main content via stdlib `html.parser`, with `<script>`/`<style>`/`<nav>`/
`<header>`/`<footer>`/`<aside>`/`<form>`/`<noscript>`/`<iframe>` stripped
and whitespace collapsed). Raw-HTML hashes are useless because most
pages mutate every load (timestamps, CSRF tokens, ad slots, A/B
variants); the extraction layer is what makes drift detection signal
rather than noise.

`baseline_hash` is the slug's first-ever `content_hash`. The first
successful poll for a slug establishes baseline (`baseline_hash ==
content_hash`, `drifted: false`). Every subsequent poll compares against
that first baseline; mismatch sets `drifted: true` and the polling
script edits the source page to add `source_drifted: true` frontmatter
plus an inline `> [!WARNING] Source drift detected` callout. The
`check_drifted_sources` lint check surfaces those flagged pages.
Drift-clear path: re-capture via clipper, ingestor regenerates the
source page from the fresh vault file, drift flag and callout disappear
naturally.

**Analysis:** `python .scripts/query_stats.py` reads the ingest and query
logs and reports page-hit frequency, exact-string + canonicalized query
repetition, dead pages (never consulted), and the bilateral amortization
ratio (cumulative query cost per page ÷ per-page ingest cost). Lint,
overview, and source-freshness telemetry have no dedicated stats script
yet — query the JSONL directly with `jq` for ad-hoc analysis.

**Commit policy.** All five JSONL files are committed to git — plain
text, inspectable, valuable as historical record at current scale.
Revisit if they grow noisy or start carrying sensitive queries.

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

#### Coverage-report syntheses (filed by `/wiki-summarize`)

Topic-coverage reports are syntheses with two optional extension fields
in addition to the standard frontmatter:

- `subtype: coverage-report` — marks the page as a topic-coverage audit
  rather than a question-answer synthesis. Distinguishes filed
  `/wiki-summarize` output from filed `/wiki-query` output. Optional;
  absent on standard syntheses.
- `coverage_anchor_slug: <slug>` — present only when retrieval was
  Shape A (topic resolved to an existing slug). The anchor slug is the
  starting point for the depth-1 neighbor walk; future freshness checks
  can watch for activity on this slug + its 1-hop neighbors.
  `null` on Shape B (fuzzy retrieval, no anchor).

Re-run convention is **overwrite** — `/wiki-summarize <topic>` files at
`syntheses/<slug>-coverage.md` and a second invocation on the same topic
overwrites the body and advances `last_updated`. Old versions live in
`git log`. Body shape is the five-section survey: Themes / Agreements /
Disagreements / Coverage gaps / Structural footprint.
