# Wiki Forge

Turn your AI agent into a knowledge compiler. Instead of asking the same
questions and re-reading the same documents every session, feed sources once
and let the LLM build a persistent, cross-referenced wiki that compounds
over time.

> **Plugin name:** `wiki-forge` — install as a Claude Code plugin or use standalone.

## The problem this solves

Every time you paste an article into an LLM chat, it reads it, gives you an
answer, and forgets everything when the session ends. Next week you paste a
related article — the LLM has no memory of the first one. No cross-references,
no contradiction detection, no accumulated understanding.

RAG (Retrieval-Augmented Generation) partially solves this by searching raw
documents at query time. But it re-derives answers from scratch every time,
loses the synthesis between sources, and can't flag when two papers disagree.

**The wiki approach is different.** Sources are read once and compiled into
structured pages — concepts, entities, comparisons — with cross-references,
confidence levels, and contradiction tracking. When you query, the LLM reads
a pre-compiled knowledge base, not raw documents. The result: faster answers,
lower token cost, zero hallucination about what your sources actually say.

> *"The wiki is think-once-retrieve-many. RAG is retrieve-then-think-every-time."*

## How it works

```
You drop a paper into vault/
    │
    ▼
/wiki-ingest processes it
    │  Extracts concepts, entities, claims
    │  Cross-references with existing pages
    │  Flags contradictions with older sources
    │  Waits for your approval before writing
    ▼
Wiki pages are created/updated
    │  sources/   — what each source says
    │  concepts/  — ideas compiled from multiple sources
    │  entities/  — people, tools, orgs
    │  comparisons/ — X vs Y side-by-side
    │  syntheses/ — saved query answers
    ▼
/wiki-query answers from compiled knowledge
    │  Cites every claim with [[wikilinks]]
    │  Surfaces contradictions between sources
    │  Flags low-confidence or stale pages
    ▼
Knowledge compounds. Every new source makes every future query better.
```

## What you can build with it

- **Research tracker** — Feed 100 papers on a topic. Query across all of them
  with citations. See where authors disagree.
- **Project brain** — Drop in architecture decisions, bug postmortems, library
  docs, product specs. The AI remembers *why* you chose PostgreSQL six months ago.
- **Course companion** — Ingest lecture notes and readings. The wiki builds
  a personalized textbook that connects concepts across sessions.
- **Competitive analysis** — Track competitors across news articles, earnings
  calls, product launches. The wiki maintains entity pages that update as new
  sources arrive.
- **Book companion** — Track characters, themes, plot threads across chapters.

Works for any domain — AI research, cooking, law, RPG campaigns. The
`/wiki-init` command asks your domain and generates matching categories.

## Quick start

### 1. Install (one-time)

**As a plugin:**

```bash
# Clone the repo
git clone https://github.com/avdiam/wiki-forge.git

# Load as a local plugin (for development/testing)
claude --plugin-dir ./wiki-forge
```

Once loaded, commands are namespaced: `/wiki-forge:init`, `/wiki-forge:ingest`, etc.

> **Plugin install only.** Skills dispatch their subagents as `wiki-forge:<agent>`
> and call helper scripts via `${CLAUDE_PLUGIN_ROOT}`. Both resolve only when
> wiki-forge is loaded as a plugin, so a manual copy into `~/.claude/skills/` +
> `~/.claude/agents/` is not supported.

### 2. Bootstrap a wiki in any project

```bash
/wiki-forge:init    # plugin
/wiki-init          # standalone
```

This creates `wiki/` and `vault/`, asks your domain, installs the schema
and helper scripts. Optionally sets up semantic search and git tracking.

### 3. Feed it sources

```bash
# Capture from URL (fetches, normalizes, ingests)
/wiki-capture https://example.com/interesting-article

# Or drop files into vault/ manually, then ingest
/wiki-ingest vault/2026-04-15-paper.md
/wiki-ingest all new
```

The ingestor presents 3-5 key takeaways and waits for your go-ahead
before touching any pages. If a source contradicts >5 existing pages,
it halts and asks you to discuss — that's paradigm-shifting material.

### 4. Query the compiled knowledge

```bash
/wiki-query What do we know about prompt caching techniques?
```

Every claim in the answer cites a wiki page via `[[wikilink]]`. If two
sources disagree, the answer says so. Substantive answers can be saved
as permanent synthesis pages that future queries will find.

### 5. Maintain quality

```bash
/wiki-lint
```

Three-pass health check: mechanical (orphans, broken links, stale pages,
graph topology) + semantic (unacknowledged contradictions, category drift)
+ a prioritized report with fix offers.

## Directory structure

After `/wiki-init`, your project looks like this:

```
project/
├── vault/                      ← YOU own this. Drop sources here.
│   ├── 2026-04-15-article.md      Immutable. The LLM never writes here.
│   └── 2026-04-16-paper.pdf
│
└── wiki/                       ← THE LLM owns this. Read freely, edit carefully.
    ├── CLAUDE.md                  Schema (auto-loaded by Claude Code)
    ├── AGENTS.md                  Same schema (for Cursor, Codex CLI, etc.)
    ├── log.md                     Append-only operation log
    ├── overview.md                Big-picture synthesis (rewritten every 5 ingests)
    ├── indexes/                   Split index files (auto-generated)
    │   ├── index.md                 Master table of contents
    │   ├── index-concepts.md        Per-type sub-indices
    │   └── ...
    ├── sources/                   One summary per vault file
    ├── concepts/                  Ideas compiled from multiple sources
    ├── entities/                  People, tools, orgs, models
    ├── comparisons/               X vs Y side-by-side
    ├── syntheses/                 Saved query answers
    ├── .scripts/                  Helper scripts (Python, stdlib only)
    └── .meta/                     Index stats, embedding cache
```

## Architecture

Two-layer invocation with smart dispatch:

| Command | What it does | Model | Cost optimization |
| --- | --- | --- | --- |
| `/wiki-init` | Bootstrap wiki + vault | — | Runs inline |
| `/wiki-capture` | Fetch URL → vault → ingest | Opus | One agent per source |
| `/wiki-ingest` | Compile vault source → wiki pages | Opus | One agent per file, sequential |
| `/wiki-query` | Answer from compiled knowledge | Sonnet | Inline for small wikis, subagent for large |
| `/wiki-lint` | Health check + graph analysis | Opus | Three-pass: mechanical → semantic → report |
| `/wiki-log` | View operation history | — | Script only, no LLM |

**Key cost optimizations:**
- Small wikis (<200 pages) handle queries inline — no subagent dispatch
- The query skill runs search in the main session (cheap bash call), then
  passes pre-filtered results to the librarian — it never reads the full index
- The ingestor uses `grep -ril` for page discovery, not index reading
- Split indices mean agents only read the sub-index they need

## Search

The wiki scales from 5 pages to 5000+ through conditional search:

| Wiki size | What happens on `/wiki-query` |
| --- | --- |
| **<200 pages** | LLM reads the sub-indices directly. No search overhead. |
| **200-2000 pages** | BM25 keyword search + ONNX semantic embeddings + RRF fusion. ~100ms. |
| **2000+ pages** | Same + optional zvec ANN index for sub-millisecond lookup. |

Search features:
- **Stopword removal** — "what is the role of attention" matches on "role" and "attention", not "what", "is", "the"
- **Tags + aliases in index entries** — pages are discoverable by alternate vocabulary
- **Snippets** — search results include 220-char context excerpts
- **Fallback** — if search scores are low, the librarian reads the sub-index manually
- **Graceful degradation** — no ONNX? BM25-only. No zvec? Brute-force cosine. No deps at all? LLM reads the index.

## Helper scripts

Pure stdlib Python (except search deps). Shipped in the plugin's `scripts/` directory (`${CLAUDE_PLUGIN_ROOT}/scripts/`), not copied into the project:

| Script | Purpose |
| --- | --- |
| `frontmatter.py` | Shared YAML frontmatter parser (imported by other scripts) |
| `update_index.py` | Regenerates split index files + `.meta/index_stats.json` |
| `append_log.py` | Structured log entries with contradiction counts |
| `lint_wiki.py` | Mechanical checks: orphans, broken links, stale, confidence, drift, broken sources |
| `graph_analyzer.py` | Wikilink topology: hubs, sinks, orphans, connected components |
| `hybrid_search.py` | BM25 + ONNX semantic + RRF fusion with tag/alias boosting |
| `ingest_source.py` | Source pre-processing: title extraction, word count, merge detection |
| `ingest_preflight.py` | All `/wiki-ingest` pre-flight checks in one call: exclusion rule, filename normalization, brief, hash dedup, pre-screen, secret scan, model pick, triage lookup |
| `ingest_commit.py` | The ingestor's write-phase close in one call: index regen, explicit-path commit with Created/Updated derived from git, log entry, verify/contradiction telemetry backfills, mechanical lint |
| `fm_edit.py` | Line-preserving frontmatter edits across pages: add source, touch, confidence, tags/aliases, symmetric `contradicts:` edges, verify-clearing |
| `classify_candidates.py` | `/wiki-corroborate` + `/wiki-resolve-contradictions` per-item close: credibility-tier classify, filter, ordered block with already-cited marks, coverage gate, telemetry row; `close` stamps the batch's transcript cost |
| `close_operation.py` | Closes every operation: parses the agent's report + `<usage>`, appends the telemetry row, commits it separately (never an amend), ticks the triage checkbox |
| `parse_agent_usage.py` | Parses Agent `<usage>` blocks; enriches token counts, model and duration from the subagent transcript |
| `setup_search.py` | Downloads ONNX model + tokenizer from HuggingFace |

All scripts work standalone from the command line — no AI tool required.

## Multi-tool compatibility

The wiki schema is installed as both `CLAUDE.md` and `AGENTS.md`:
- **Claude Code** — full support. Reads `CLAUDE.md`, runs skills, dispatches subagents.
- **Codex CLI, Cursor, Gemini CLI, OpenCode** — schema-aware. These tools
  auto-load `AGENTS.md` and can read/understand the wiki structure, but
  the operational workflows (skills, subagent dispatch) are Claude Code-specific.
  Manual wiki edits following the schema will work with any tool.

## Requirements

**Core (zero external deps):**
- Python 3.9+
- Git (recommended — agents commit after every operation)

**Semantic search (optional, ~100MB):**
```bash
pip install onnxruntime tokenizers numpy
python ${CLAUDE_PLUGIN_ROOT}/scripts/setup_search.py   # downloads 30MB ONNX model
```

**Fast ANN search (optional, +45MB — for large wikis):**
```bash
pip install zvec
```

**Reading experience (optional):**
- [Obsidian](https://obsidian.md) — graph view, `[[wikilink]]` resolution,
  live preview. The wiki is plain markdown and works anywhere, but Obsidian
  makes the cross-references visual.

## Best practices

1. **Start small.** Feed 5-10 high-quality sources first. Let the wiki
   establish its taxonomy before scaling up. Batch-dumping hundreds of files
   on day one produces shallow, poorly cross-referenced pages.

2. **Git is your safety net.** The agents commit after every operation. If
   an ingest goes wrong, `git diff` shows exactly what changed and `git revert`
   fixes it.

3. **Tags and aliases matter.** Every page should have 3-8 tags and all its
   acronyms/alternate names as aliases. These appear in the index and directly
   affect search quality. A page that can't be found is a page that doesn't exist.

4. **Let knowledge compound.** The power is in cross-referencing. A concept
   page that synthesizes evidence from 15 sources is worth more than 15
   individual summaries. The ingestor is designed to *edit many, create few*.

5. **Run `/wiki-lint` after batch ingests.** It catches orphans, broken links,
   contradictions, and structural problems that individual ingests can miss.

## Credits

Based on synthesis from:
- [Andrej Karpathy's original `llm-wiki.md` gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (concept)
- [Balu Kosuri's hands-on implementation](https://medium.com/@k.balu124/i-used-karpathys-llm-wiki-to-build-a-knowledge-base-that-maintains-itself-with-ai-df968e4f5ea0) (repo + workflow)
- ["Andrej Karpathy Stopped Using AI to Write Code"](https://medium.com/neuralnotions/andrej-karpathy-stopped-using-ai-to-write-code-hes-using-it-to-build-a-second-brain-instead-cddceadc5df5) (architecture details)
- [Reza Rezvani's production Claude Code skill](https://github.com/alirezarezvani/claude-skills) (subagents, scripts, schema)
