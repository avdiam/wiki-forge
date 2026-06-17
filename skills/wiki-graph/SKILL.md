---
name: wiki-graph
description: Run path / neighbor / subgraph queries over the wiki's wikilink graph. Deterministic stdlib output (no librarian dispatch, no model call). Use when the user runs /wiki-graph, asks "how does X connect to Y", "what's near X", "N-hop neighbors of X", or wants the structural connection rather than a synthesized prose answer.
argument-hint: "<path|neighbors|subgraph> <slug> [<slug>] [--depth N] [--json]"
---

# wiki-graph

User-facing graph traversal over the wiki's wikilink topology. Three subcommands wrap `${CLAUDE_PLUGIN_ROOT}/scripts/graph_query.py`:

- `path A B` — shortest path between two slugs (undirected).
- `neighbors X [--depth N]` — pages within N hops of X. Default depth 1 (the wiki is dense; depth 2 from a hub commonly reaches most of the graph).
- `subgraph X` — full connected component containing X.

Walks the **wikilink graph only** (page-body `[[wikilinks]]`); typed-edge frontmatter (`contradicts:` / `supersedes:` / `superseded_by:`) is excluded by default. Pure script output — no librarian, no synthesis, no token cost beyond this skill's prompt.

*Architecturally: Option C (inline tool-orchestration) per `_template-simple/SKILL.md`. No subagent dispatch.*

> Direct invocation. For a natural-language entry point that auto-routes between this, `/wiki-query`, and `/wiki-summarize`, see `/wiki-ask`.

## Workflow

### 1. Locate the wiki
Find the wiki root in the current directory or upward (look for `wiki/CLAUDE.md`).

### 2. Parse arguments
First positional → subcommand (`path` | `neighbors` | `subgraph`).
Slug arguments accept bare slugs, `[[wikilink]]` form, or `<slug>.md`. The script normalizes.

If the user passed a question in plain English instead of a subcommand, classify and translate:

- "how does X connect to Y" / "path between X and Y" → `path X Y`
- "what's near X" / "neighbors of X" / "N-hop around X" → `neighbors X [--depth N]` (default 1; user-requested "N-hop" overrides)
- "subgraph around X" / "cluster of X" / "connected to X" → `subgraph X`

When translating, surface the translation inline before invoking, so the dispatch is auditable:

> Routed to: `/wiki-graph neighbors lethal-trifecta --depth 2`

If a slug doesn't resolve unambiguously (e.g., "Karpathy" matches both `andrej-karpathy` and `karpathy-llm-wiki-pattern`), ask a single bounded multiple-choice clarification before invoking. Fall through with a clear error if intent is genuinely vague.

### 3. Run graph_query.py
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/graph_query.py" <wiki> <op> <args>
```

Examples:
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/graph_query.py" wiki path andrej-karpathy graphify
python "${CLAUDE_PLUGIN_ROOT}/scripts/graph_query.py" wiki neighbors lethal-trifecta --depth 2
python "${CLAUDE_PLUGIN_ROOT}/scripts/graph_query.py" wiki subgraph llm-wiki-pattern
```

Add `--json` if the user asked for machine-readable output, or if downstream chaining wants structured fields.

### 4. Show the result to the user
Default output is human-readable markdown with `[[wikilinks]]` so Obsidian renders them clickable. Pass through verbatim — do not synthesize prose around the result. The script's output IS the answer.

For very large `subgraph` results (>40 edges) the script truncates with a `… (N more)` line. If the user asks for the full set, re-run with `--json`.

### 5. Log query telemetry
Write one row to `wiki/.meta/query_log.jsonl` so the operation is visible in `query_stats.py` analyses and Phase-2 router design has real `/wiki-graph` invocation data.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_query_log.py" --wiki <wiki> \
  --query-skill graph \
  --query "<op> <slug-args>" \
  --pages-consulted "<comma-sep result slugs>" \
  --duration-ms <wall_clock_ms>
```

Notes:
- `--query-skill graph` distinguishes this row from `/wiki-query` rows.
- `--query` is the raw subcommand + args (e.g., `"neighbors lethal-trifecta --depth 2"`), not a question.
- `--pages-consulted` is the result set: path slugs for `path`; all neighbor slugs for `neighbors`. **For `subgraph`, cap the logged set** — pass the anchor slug plus at most the first 40 member slugs, and append a `+<N>-more` sentinel token when the component is larger (e.g. `--pages-consulted "rag,agent-harness,…,+632-more"`). A `subgraph` anchored in the giant component returns essentially the whole wiki (a `rag` anchor reaches 672 pages / 4403 edges), and writing all ~672 slugs bloats `query_log.jsonl` with a near-useless full-graph dump. The 40-node cap mirrors the >40-edge display truncation in step 4; the sentinel preserves the total count for any later analysis.
- `--search-mode` is omitted (graph rows are null on that field).
- All cost fields (`--total-tokens`, `--tool-uses`, etc.) are omitted (no model call).
- `--duration-ms` comes from the script's `duration_ms` field in `--json` output, OR a main-session wall-clock measurement if running without `--json`.
- If this invocation was routed from `/wiki-ask`, add `--routed-from ask`.

## Rules

- **Read-only.** Never modify wiki pages.
- **No librarian.** Graph queries are deterministic stdlib; do not dispatch a subagent.
- **No synthesis.** The script's output is the answer. Don't paraphrase, summarize, or add framing — the user asked a structural question, not a prose question.
- **Default depth = 1** for `neighbors`. The wiki is dense (363 pages, 2 components, hubs at 152/106/71 inbound) — depth-2 from a moderately-connected slug commonly reaches most of the graph (`agent-harness` measured at 120 pages depth-1 → 350 pages depth-2 = 96% of the corpus). Default-1 returns the immediate-neighborhood signal that's actually informative; users wanting wider scope opt in via `--depth 2` (or 3) explicitly. Mirrors the read-vs-write asymmetry already established for the Phase-2 router: default to the cheap/narrow option, let users opt into more.
- **Slug ambiguity → ask once.** Single bounded multiple-choice; never chain follow-ups.
- **Genuinely vague question → fall through.** If the user's input doesn't match any of the three subcommands and slug resolution can't disambiguate, suggest they re-ask via `/wiki-query` for prose synthesis.
- **No edits to log.md.** Graph queries do not produce a `## [<timestamp>] <op>` log entry — `query_log.jsonl` is the audit trail for read operations. Same as `/wiki-query`.
