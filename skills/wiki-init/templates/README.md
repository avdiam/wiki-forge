# Wiki — README

This wiki is the egress layer of a two-stage AI pipeline: raw material lands in `vault/` (immutable), an LLM agent compiles it into this directory (`wiki/`) under a strict schema, and humans browse the result (typically in Obsidian).

This file is the human-facing orientation. The operational rules and templates the agent follows live elsewhere — see the index below.

## How this wiki is organized

| File | Purpose |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Operational rules, page types, frontmatter schema, cross-reference rules, page templates. Read directly by the agents that write pages. |
| [`CATEGORIES.md`](CATEGORIES.md) | The authoritative category taxonomy + procedure for adding a new category. `lint_wiki.py` reads this file at runtime. |
| [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) | Schema change history. |
| [`WORKFLOWS.md`](WORKFLOWS.md) | Manual-execution playbook for AI platforms without native `/skills` support. |
| `CLAUDE.md` | `@AGENTS.md` pointer for tools that look for `CLAUDE.md`. Nested `CLAUDE.md` files load only on demand, so agents that write pages Read `AGENTS.md` directly. |

## Architecture: three layers

1. **`vault/`** — Immutable source material (articles, papers, notes, transcripts). Read-only from the agent's perspective. Humans add files here. Sibling to `wiki/`.
2. **`wiki/`** — Agent-maintained wiki pages. The agent creates, updates, and cross-references. Humans read; direct edits are allowed but not the primary mode.
3. **`AGENTS.md` + the `wiki-*` skills** — The schema and operational rules that turn the agent into a disciplined maintainer.

## Tooling compatibility

The schema lives in `AGENTS.md` (canonical content); `CLAUDE.md` is a one-line `@AGENTS.md` pointer. The dual-file shape lets tools that look for `AGENTS.md` by convention (Codex CLI, Cursor, Antigravity, Gemini CLI, OpenCode) and tools that look for `CLAUDE.md` both find the schema. Edit `AGENTS.md` only.

If your AI platform does not natively support Claude Code's `/skills` commands or subagent dispatch, refer to [`WORKFLOWS.md`](WORKFLOWS.md) for step-by-step manual execution instructions. All operations can be performed using standard terminal commands and the helper scripts shipped in the wiki-forge plugin's `scripts/` directory.

## Special files

All three are maintained by scripts or agents; never hand-edit them.

- **`pages/indexes/index.md`** — the master index, pointing to one sub-index per page type (`index-concepts.md`, `index-entities.md`, …). Within each sub-index, pages are grouped by category, one line each, with aliases and tags visible to both the LLM and search:

  ```text
  - [[slug]] — one-line description `[aka: alt-name, acronym | tags: topic, domain, technique]` _(confidence: 0.8, sources: 3)_
  ```

  The `aka:` and `tags:` fields make pages discoverable by alternate vocabulary: a query for "kv-cache" finds "Prompt Caching" because the tag is in the index line. Regenerated on every operation by `update_index.py`.
- **`log.md`** — append-only operation log, written by `append_log.py`:

  ```markdown
  ## [2026-04-15 14:30] ingest | Karpathy LLM Wiki gist
  - Created: pages/sources/2026-04-15-karpathy-llm-wiki.md
  - Updated: pages/concepts/rag.md (added contradiction section)
  - Commit: a1b2c3d
  ```
- **`overview.md`** — one page of prose on the wiki's current state: major themes, recent additions, unresolved questions, interesting connections. Refreshed every 10 ingests by `/wiki-overview --soft`; a full rewrite (`/wiki-overview --full`) is trigger-detected by `check_overview_triggers.py` or user-invoked, with a section-by-section approval gate. The `wiki-curator` agent owns the writes.

## Script-API reference

Each script in the wiki-forge plugin's `scripts/` directory documents its own contract via module docstring + argparse `--help` (`python ${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py --help`). Specifically:

- Telemetry JSONL row schemas live in their owner `append_*_log.py` (`append_ingest_log.py`, `append_query_log.py`, `append_lint_log.py`, `append_overview_log.py`, `append_freshness_log.py`, `append_corroborate_log.py`). Skills never call them by hand: `close_operation.py <ingest|lint|overview|query|summarize|graph|ask>` parses the agent report and `<usage>` block (via `parse_agent_usage.py`), runs the appender and commits the row separately.
- `.meta/lint-config.json` schema lives in `lint_wiki.py`; `.meta/secret-patterns.json` in `check_secrets.py`.
- `hybrid_search.py` and `graph_analyzer.py` document their modes at the top of each script.

## Directory layout

```text
vault/                  # Immutable sources; filename: YYYY-MM-DD-slug.md
wiki/
├── README.md           # This file — human orientation
├── CLAUDE.md           # @AGENTS.md pointer (for tools that look for CLAUDE.md)
├── AGENTS.md           # Operational rules + page templates (the schema)
├── CATEGORIES.md       # Authoritative category taxonomy (runtime-read by lint_wiki.py)
├── SCHEMA_CHANGELOG.md # Schema change history
├── WORKFLOWS.md        # Manual-execution playbook for non-Claude-Code tools
├── log.md              # Append-only chronological operation log
├── overview.md         # Evolving big-picture synthesis (refreshed via /wiki-overview — every 10 ingests soft; full rewrite trigger-detected or user-invoked)
├── .meta/              # Sidecar data (index stats, embedding cache, telemetry logs)
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
