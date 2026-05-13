---
schema_version: 2
not_a_source: true
last_updated: 2026-05-13
---

# Wiki — Categories (authoritative taxonomy)

> **This file is the authoritative source for the wiki's category taxonomy.** As of the 2026-05-13 schema decomposition, `lint_wiki.py` parses this file at runtime (see `load_authoritative_categories` in `.scripts/lint_wiki.py`) — the in-script `AUTHORITATIVE_CATEGORIES` constant is gone. To add a new category, edit this file + bump the schema version in [`AGENTS.md`](AGENTS.md) + add a [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) entry. See "Adding a new category" below.

Categories are an **enumerated, lint-enforced taxonomy**. Every page's `category:` frontmatter must be one of the values listed below.

`lint_wiki.py`'s `unknown_category` check rejects any page whose `category:` is outside this list with a hard error.

## Authoritative category list

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

## Adding a new category

When a source introduces material that doesn't fit any existing category, the agent **does not silently create a new category**. The flow is:

1. **First, check if an existing category can absorb the page.** If `coding-agents` is now sharing space with notes on general AI assistants, propose renaming it. Breadth > proliferation.
2. **If a new category is genuinely needed**, surface this to the user at the `/wiki-ingest` takeaway approval gate (step 11) under `### New categories proposed`. State:
   - The proposed category name (kebab-case)
   - Why existing categories don't fit
   - Which pages would go in it (must be ≥2 — never create a single-page category)
3. **The user must approve, and the schema must be edited.** When approved, the user (or the agent at the user's direction) edits **this file** (`CATEGORIES.md`) AND bumps the `Schema version` banner in [`AGENTS.md`](AGENTS.md) AND adds a [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) entry. Only after the schema bump can the agent create the new pages.
4. **Never create a category with a single page.** Either fold into an existing category or wait for a second source — the schema bump is itself gated on ≥2 pages worth of material.

## Parser format requirements

`lint_wiki.py`'s `load_authoritative_categories` parser expects:

- The literal section header `## Authoritative category list` (H2).
- Bullet lines of the form `` - `<slug>` — <description> ``: leading `- `, then a backtick-wrapped kebab-case slug, then the rest of the line.
- The list ends at the next `#`-prefixed header.

Keep the format consistent when adding entries — the parser is intentionally simple and will skip lines it can't parse.
