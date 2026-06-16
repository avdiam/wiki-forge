# LLM Wiki Schema

> **Schema version:** 4 — see [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) for change history. Bump on breaking changes (new required frontmatter field, renamed page type, removed operation, changed slug format, category-structure change, **category-list addition or removal** since the list is now enumerated and lint-enforced). Non-breaking changes (template tweaks, rule clarifications, prose edits) don't bump. `CLAUDE.md` is the Claude Code auto-load anchor and `@-includes` this file; [`CATEGORIES.md`](CATEGORIES.md) is a sibling schema file (see [`README.md`](README.md) for the full layout).

This file is the operational schema for the wiki in this directory. It is auto-loaded into the agent's context (via `CLAUDE.md`'s `@AGENTS.md` include) and is referenced by the `wiki-*` skills. Follow the rules and templates here exactly.

Content lives in `pages/sources/`, `pages/concepts/`, `pages/entities/`, `pages/comparisons/`, `pages/syntheses/`. For the full directory tree, the three-layer architecture, and notes on running this wiki with non-Claude-Code AI platforms, see [`README.md`](README.md).

## Page types

### Source summary (`pages/sources/`)

One per vault file. Captures what the source says, without cross-source synthesis.

- Filename: same slug as the vault file
- Example: `pages/sources/2026-04-15-karpathy-llm-wiki.md`

### Concept (`pages/concepts/`)

An idea, pattern, method, or theory. Accumulates evidence from multiple sources
over time. This is where most retrieval happens.

- Filename: kebab-case of the concept name
- Examples: `rag.md`, `llm-wiki-pattern.md`, `prompt-caching.md`

### Entity (`pages/entities/`)

A specific named thing: a person, tool, service, organization, product, or
model family.

- Filename: kebab-case
- Examples: `claude-code.md`, `andrej-karpathy.md`, `langchain.md`, `claude-4-6.md`

### Comparison (`pages/comparisons/`)

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

### Synthesis (`pages/syntheses/`)

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
- **source_type** — *Required on `pages/sources/` pages; not used elsewhere.* The
  raw-material category — one of `article`, `paper`, `gist`, `blog`, `docs`,
  `transcript`, `notes`, `other`. Copied verbatim from the raw vault file's
  frontmatter (which the `wiki-capture` skill writes on capture). Helps
  calibrate trust — a claim from a peer-reviewed paper carries different
  weight than the same claim from a blog post, even at identical confidence.
- **captured** — *Required on `pages/sources/` pages; not used elsewhere.* ISO date
  the raw source material was pulled into `vault/`. Copied from the raw vault
  file's frontmatter (`captured:` written by `/wiki-capture`, or `clipped:`
  written by the clipper — ingestor accepts either and writes `captured:` on
  the source page). Distinct from `last_updated` (the wiki-page edit date).
  Drives the `stale_source` lint check — source pages whose `captured:` is
  older than 180 days surface as a review queue, signaling upstream content
  may have drifted since the original capture (see [[llm-wiki-failure-modes]]
  #8 on dynamic-data mismatch).
- **published** — *Optional on `pages/sources/` pages; not used elsewhere.* ISO date
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
- **source_hash** — *Required on `pages/sources/` pages; not used elsewhere.* First
  16 hex characters of the raw vault file's SHA-256 (`sha256_short` from
  `ingest_source.py`'s JSON brief). 64 bits is far past birthday-bound
  collision risk for any personal knowledge base — the short form is both
  inspectable in frontmatter and sufficient for dedup. Drives content-based
  dedup: before every ingest, the `/wiki-ingest` skill greps `pages/sources/*.md`
  frontmatter for a matching `source_hash:` and prompts the user on match.
  Robust to filename / URL / date-slug variations that slug-based and
  URL-based dedup miss.
- **status** — *Optional on `pages/entities/` pages; not used elsewhere.* Tool /
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
- **stub_origin** — *Optional, any page type.* Forward-only marker set by
  `/wiki-stub` indicating the page was created as a low-confidence
  placeholder. Three values:
  - `hub` — created via `/wiki-stub <slug>` to anchor N≥10 incoming
    wikilinks pending a dedicated source. `sources: []` is permitted on
    creation (zero sources is the defining property).
  - `single-source` — created via the `/wiki-ingest` takeaway gate's
    single-source CREATE path (or `/wiki-stub <slug> --pattern
    single-source --source <vault-path>`); exactly one source at creation.
  - `planned` — created via `/wiki-stub <slug> --pattern planned --reason
    "..."` as a user-declared research target with zero sources at
    creation. `sources: []` is permitted.

  All three pattern values pair with `verify: true` and one of the three
  verbatim stub callouts (see § Templates → Stub callout templates).
  `check_drifted_stubs` lint surfaces shape-drifted pages — body matches
  a stub callout but `verify: true` is absent. `check_unresolved_link_hubs`
  surfaces slugs with ≥10 unresolved inbound wikilinks (the stub-candidate
  queue for the `hub` pattern). Pages predating this rule (May 2026) are
  not retroactively backfilled — the field is forward-only and absence on
  legacy stubs is not a lint event.
- **claim-tag inline markers** — *Optional, body-only, applies to concept /
  entity / comparison / synthesis pages.* Italicized markers that follow a
  claim to declare its evidentiary kind. Two values:
  - `*[INFERRED]*` — claim is the ingestor's synthesis across multiple
    sources, or a judgment-call inference from one source. Apply when the
    claim is plausibly derivable from cited sources but is not a direct
    extraction.
  - `*[AMBIGUOUS]*` — claim is contested between sources, single-source
    without independent corroboration, or otherwise deserves reader
    scrutiny. Apply when the page-level `verify: true` flag is too coarse
    — i.e., when one specific claim is uncertain and the rest of the page
    is solid.

  **Untagged claims are implicitly `EXTRACTED`** — directly attested in a
  cited source. The convention is flag-as-exception (mirrors `verify:`):
  apply markers only when a claim is not directly extracted, not as a
  default annotation on every sentence.

  **Format.** Place the marker immediately after the claim's terminal
  punctuation, separated by one space. Example:

  > Karpathy framed the wiki as a "personal LLM context engine." Mark
  > Chen extends this to two-domain wikis. *[INFERRED]*

  **Source pages are exempt.** Source summary pages carry source fidelity
  by definition — every claim is verbatim or near-verbatim from the source
  they summarize.

  **Lint surface.** The `excessive_ambiguous` check counts `*[AMBIGUOUS]*`
  markers per page and flags pages with more than 3 as a review queue
  (similar shape to `needs_verification`). High AMBIGUOUS density signals a
  page that needs corroborating sources, not a page that needs rewriting —
  distinct from `stale: true` and `verify: true` (page-level signals).

  **Claim-ID anchors** — *Optional, opt-in, same body-only scope as the
  markers above.* Make a specific claim *addressable* by appending
  `{#claim-<slug>}` (slug = lowercase `[a-z0-9-]+`) immediately after its
  terminal punctuation — after any `*[TAG]*` marker, separated by one space.
  Add an ID **by judgment**, only to a claim worth referencing directly (one
  likely to be traced, cited across pages, or subject to a contradiction
  cascade) — **not** to every sentence, exactly like the flag-as-exception
  philosophy of the tag markers. Applies to EXTRACTED and tagged claims alike.
  Forward-only: legacy claims are not retroactively anchored, and absence is
  never a lint event.

  > Mark Chen extends this to two-domain wikis. *[INFERRED]* {#claim-chen-two-domain}
  > Claude Code shipped agent teams in 2026. {#claim-cc-agent-teams-2026}

  The anchor is the stable input granularity for **`/wiki-trace`**: instead of
  pasting a quote, reference the claim as `[[page-slug#claim-<slug>]]` and the
  skill resolves it to the claim text, then traces that into the raw vault.
  `lint_wiki.py`'s `claim_id_issues` check enforces per-page uniqueness (so the
  reference resolves to a single claim) and well-formed slugs.

  Anchored claims also feed `lint_wiki.py`'s `cascade_review` check (the
  evidence-chain cascade): for an anchored claim that is *at risk* (page
  `verify: true`, the claim tagged AMBIGUOUS/INFERRED, or the page
  contradicted), the lint reports its **blast radius** — the other claims/pages
  resting on the *same source* — so a refuted source surfaces every dependent
  claim, not just the one being contradicted. The claim's source is pinned by
  precedence: **inline attribution** (an adjacent quote blockquote ending
  `— [[source-slug]]`) → verbatim trace → single-source page. So when a claim
  is uncertain, attaching its supporting quote with a `— [[source]]` wikilink
  isn't just good citation — it's what lets the cascade attribute that claim
  precisely on a multi-source page (without it, a synthesized multi-source
  claim is reported as unattributable rather than guessed).

  **Crystallization writes these markers too.** `/wiki-crystallize <synthesis-slug>`
  is the 4th canonical operation (Ingest → Query → Summarize → **Crystallize** →
  Lint): it lifts the atomic facts a synthesis page surfaced and merges each into
  the existing concept/entity page that should own it, tagged + anchored by the
  conventions above. A fact that verbatim-traces to a cited source merges *untagged*
  (implicit EXTRACTED) with `sources:` pinned to that source; a synthesized fact
  merges `*[INFERRED]*` against the synthesis's underlying sources. Every
  crystallized fact gets a `{#claim-id}` so the cascade tracks it. Merge-only
  (homeless facts are reported, never auto-created) and human-gated (a fan-out
  approval table before any write). See `skills/wiki-crystallize/SKILL.md`.

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

The authoritative category list and the procedure for adding a new category live in [`CATEGORIES.md`](CATEGORIES.md). `lint_wiki.py`'s `check_unknown_category` parses that file at runtime — it's the single source of truth as of 2026-05-13.

A page's `category:` frontmatter must be one of the values in that list; lint rejects anything else as a hard error. Never silently introduce a new category — see `CATEGORIES.md` § "Adding a new category" for the approval flow + schema-bump procedure.

## Creating new pages vs editing existing (the most important rule)

When processing a source, for each concept/entity you identify:

1. **Run a fast context query** for existing matches. Use `grep -ril` across
   `pages/sources/`, `pages/concepts/`, `pages/entities/`, `pages/comparisons/`, `pages/syntheses/` for fast verification. Do not load the full index.
2. **Glob `<type>/`** (e.g., `pages/concepts/`) to catch anything grep might have missed.
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
  CREATE entries (e.g., `CREATE pages/concepts/foo.md — single source; recommend
  verify: true`) and the user's approval reply ("yes promote — this is
  canonical / single-source ok") clears the requirement.

Otherwise: fold the information into the most relevant existing page with
a note, and wait for a second source before promoting.

The `single_source_unverified` lint check surfaces violations as a
soft-warning review queue. Existing pages predating this rule are part of
that queue and can be cleaned up incrementally — the rule is not
retroactive-blocking.

### Stub pages (codified via `/wiki-stub`)

Three structurally distinct stub-creation patterns exist; all three live
under one skill and one frontmatter marker (`stub_origin:`):

- **`hub`** — A slug accumulates ≥10 incoming wikilinks across ingests
  without ever getting a dedicated source. The graph forms a phantom hub.
  Invoke `/wiki-stub <slug>` to anchor the hub with an honest low-confidence
  placeholder. `confidence: 0.4`, `sources: []`, `verify: true`,
  `stub_origin: hub` — plus the verbatim hub callout and a `## Why this
  stub exists rather than silent absorption` section.
- **`single-source`** — A source treats an entity in substance, but only
  that one source has. Standalone-with-`verify: true` per the rule above.
  Created in-band during `/wiki-ingest`'s takeaway gate, or out-of-band
  via `/wiki-stub <slug> --pattern single-source --source <vault-path>`.
  `confidence: 0.5`, `verify: true`, `stub_origin: single-source` — plus
  the verbatim single-source callout.
- **`planned`** — User pre-emptively declares a slug as a research target
  with no inbound graph pressure and no captured source. Invoke
  `/wiki-stub <slug> --pattern planned --reason "<one-liner>"`.
  `confidence: 0.3`, `sources: []`, `verify: true`, `stub_origin: planned`
  — plus the verbatim planned callout.

The skill, the three callout contracts, and the lint checks that catch
shape drift are documented at `.claude/skills/wiki-stub/SKILL.md`. Use
`/wiki-stub --list` to display candidate slugs surfaced by
`check_unresolved_link_hubs`.

**Expect ingest to edit many pages and create few.** Karpathy's own wikis
touch 10–15 existing pages per new source. If your ingest creates 10 pages
and edits 0, you probably missed connections — re-check via grep tools.

### Concept-hub digest discipline (decomposing an oversized hub)

A concept hub that accumulates one `### From <source>` evidence block per
ingest will eventually cross the **~25K-word Read-limit proxy** (the 25K-token
cap on the Read tool) — at which point routine ingests on adjacent topics can
no longer Read it whole. When a hub approaches that size, decompose it by
**EXTRACTING evidence clusters, not COMPRESSING prose:**

1. **Extract, don't summarize-in-place.** The accumulated mass is usually
   load-bearing distinct synthesis (each block contributes a genuinely
   different framing; blocks cross-reference and disambiguate each other), not
   redundancy. A naive per-author consolidation pass replays the pull-quote
   information-loss failure the Mode B preservation rule exists to prevent.
2. **Shed each cluster to the page that should own it** — a spawned sub-concept
   page (e.g. a hub's component-taxonomy reconciliation → its own concept), an
   existing concept/comparison that already covers it, or the `pages/sources/`
   page that owns the full source summary. **Grep before creating** (the
   create-vs-edit rule above): a cluster's natural home often already exists.
3. **Leave a synthesis-delta digest + `[[pointer]]` where the cluster was.**
   The bulk relocates; what stays on the hub is a one-paragraph digest of *why
   the cluster mattered to this concept* plus the pointer. **Every claim must
   live on a destination page before it leaves the hub** — if a claim has no
   clean home, leave it on the hub; never drop it (Karhade rule).
4. **Reduce the hub to its conceptual spine** — definition, the core framing,
   the load-bearing empirical anchors, and the digests — so it reads as the
   map, with the territory on the spawned/destination pages.

The pattern is repeatable: when one hub crosses the proxy, its trailing
siblings usually will too (size accretes on the same one-block-per-ingest
dynamic) — so treat the decomposition as a reusable move (extract → digest →
pointer), not a one-off rescue of a single page.

## Cross-references

Use Obsidian-style `[[wikilinks]]` for every internal reference. Targets
resolve by slug, not full path:

- `[[rag]]` → `pages/concepts/rag.md`
- `[[andrej-karpathy]]` → `pages/entities/karpathy.md`
- `[[rag-vs-llm-wiki]]` → `pages/comparisons/rag-vs-llm-wiki.md`

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

### `pages/indexes/index.md` (master index)

The navigation spine. Acts as a master directory pointing to sub-indices
for each type (`pages/indexes/index-concepts.md`, `pages/indexes/index-entities.md`, etc.).

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
- Created: pages/sources/2026-04-15-karpathy-llm-wiki.md
- Created: pages/concepts/llm-wiki-pattern.md
- Updated: pages/concepts/rag.md (added contradiction section)
- Updated: pages/entities/karpathy.md (source added, confidence 0.8→0.9)
- Commit: a1b2c3d
```

### `overview.md`

A single page of prose describing the current state of the wiki — major themes, recent additions, unresolved questions, interesting connections.

Mechanically refreshed every 10 ingests via `/wiki-overview --soft` (Mode A); full rewrite via `/wiki-overview --full` (Mode B) is trigger-detected (via `check_overview_triggers.py`) or user-invoked, with a section-by-section approval gate. See [`.claude/agents/wiki-curator.md`](../.claude/agents/wiki-curator.md) for the operational spec.

## Script-API reference

Each script in `.scripts/` documents its own contract via module docstring + argparse `--help`. Run `python wiki/.scripts/<script>.py --help` for usage; read the script's docstring for the schema of any sidecar config or JSONL row it owns. Specifically:

- Telemetry JSONL row schemas live in their owner `append_*_log.py` (six logs: `append_ingest_log.py`, `append_query_log.py`, `append_lint_log.py`, `append_overview_log.py`, `append_freshness_log.py`, `append_corroborate_log.py`).
- `.meta/lint-config.json` schema lives in `lint_wiki.py`.
- `.meta/secret-patterns.json` schema lives in `check_secrets.py`.
- `hybrid_search.py` and `graph_analyzer.py` document their modes and usage at the top of each script.

## Multi-tool compatibility

This schema lives in `AGENTS.md` (the canonical content); `CLAUDE.md` is an `@-include` pointer for Claude Code's auto-load. The dual-file shape lets tools that look for `AGENTS.md` by convention (Codex CLI, Cursor, Antigravity, Gemini CLI, OpenCode) and tools that look for `CLAUDE.md` (Claude Code) both find the schema. Edit `AGENTS.md`; `CLAUDE.md` resolves the include automatically.

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

### Stub callout templates

Pages created via `/wiki-stub` carry one of three verbatim callouts at the
top of the body (immediately after the H1). The exact phrasing is a HARD
contract — `check_drifted_stubs` and search/retrieval both depend on the
verbatim string matching. The skill writes these; the templates below are
the canonical reference.

**Hub stub (`stub_origin: hub`)** — body lines 1-2 after the H1:

```markdown
# <Entity Name>

> ⚠ needs verification — stub: anchoring <N> incoming wikilinks pending a dedicated source.
```

Plus a `## Why this stub exists rather than silent absorption` section
near the end of the body (verbatim template body; interpolate `<N>` and
`<Entity Name>`):

```markdown
## Why this stub exists rather than silent absorption

Our schema's >3-sentence substantial-coverage threshold would normally argue for folding incidental mentions into parent pages. That rule breaks down when cumulative incoming wikilinks make the entity a *discovered graph hub* — <N> pointers at a nonexistent page is stronger signal than any single source alone. The stub with `verify: true` + `confidence: 0.4` + this explicit callout is the honest middle: an anchor for the graph, not a factual claim about <Entity Name>.
```

**Single-source stub (`stub_origin: single-source`)** — body lines 1-2 after the H1:

```markdown
# <Entity Name>

⚠ needs verification: single-source entity stub. <one-sentence gap descriptor — what's missing beyond the cited source>.
```

No closing section required — single-source stubs are typically short.

**Planned stub (`stub_origin: planned`)** — body lines 1-2 after the H1:

```markdown
# <Entity Name>

⚠ planned research target — no sources captured yet. Reason: <user-supplied one-liner>.
```

No closing section required — the page is a research placeholder, body
fleshes out when the first source lands.

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
`pages/syntheses/<slug>-coverage.md` and a second invocation on the same topic
overwrites the body and advances `last_updated`. Old versions live in
`git log`. Body shape is the five-section survey: Themes / Agreements /
Disagreements / Coverage gaps / Structural footprint.
