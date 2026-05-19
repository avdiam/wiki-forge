# Wiki — README

This wiki is the egress layer of a two-stage AI pipeline: raw material lands in `vault/` (immutable), an LLM agent compiles it into this directory (`wiki/`) under a strict schema, and humans browse the result (typically in Obsidian).

This file is the human-facing orientation. The operational rules and templates the agent follows live elsewhere — see the index below.

## How this wiki is organized

| File | Purpose |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Operational rules, page types, frontmatter schema, cross-reference rules, page templates. Auto-loaded into the agent's context. |
| [`CATEGORIES.md`](CATEGORIES.md) | The authoritative category taxonomy + procedure for adding a new category. `lint_wiki.py` reads this file at runtime. |
| [`SPECIFICATION.md`](SPECIFICATION.md) | Script-API reference — telemetry JSONL schemas, sidecar config files, helper-script contracts. Not auto-loaded; not consulted by any agent at runtime. |
| [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) | Schema change history. |
| [`WORKFLOWS.md`](WORKFLOWS.md) | Manual-execution playbook for AI platforms without native `/skills` support. |
| `CLAUDE.md` | Auto-load anchor for Claude Code; `@-includes` `AGENTS.md`. |

## Architecture: three layers

1. **`vault/`** — Immutable source material (articles, papers, notes, transcripts). Read-only from the agent's perspective. Humans add files here. Sibling to `wiki/`.
2. **`wiki/`** — Agent-maintained wiki pages. The agent creates, updates, and cross-references. Humans read; direct edits are allowed but not the primary mode.
3. **`AGENTS.md` + the `wiki-*` skills** — The schema and operational rules that turn the agent into a disciplined maintainer.

## Tooling compatibility

If your AI platform (Cursor, Windsurf, Gemini, etc.) does not natively support Claude Code's `/skills` commands or subagent dispatch, refer to [`WORKFLOWS.md`](WORKFLOWS.md) for step-by-step manual execution instructions. All operations can be performed using standard terminal commands and the helper scripts in `.scripts/`.

## Directory layout

```text
vault/                  # Immutable sources; filename: YYYY-MM-DD-slug.md
wiki/
├── README.md           # This file — human orientation
├── CLAUDE.md           # Auto-load anchor for Claude Code (@-includes AGENTS.md)
├── AGENTS.md           # Operational rules + page templates
├── CATEGORIES.md       # Authoritative category taxonomy (runtime-read by lint_wiki.py)
├── SPECIFICATION.md    # Script-API reference (telemetry, sidecar configs)
├── SCHEMA_CHANGELOG.md # Schema change history
├── WORKFLOWS.md        # Manual-execution playbook for non-Claude-Code tools
├── log.md              # Append-only chronological operation log
├── overview.md         # Evolving big-picture synthesis (refreshed via /wiki-overview — every 10 ingests soft; full rewrite trigger-detected or user-invoked)
├── .meta/              # Sidecar data (index stats, embedding cache, telemetry logs)
├── .scripts/           # Helper scripts (update_index, lint, search, etc.)
│   └── models/         # ONNX model for semantic search (optional)
└── pages/              # Knowledge content (separated from project/schema files above)
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
