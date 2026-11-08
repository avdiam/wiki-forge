---
name: wiki-ask
description: Natural-language entry point for read-side wiki queries. Classifies a plain-English question and routes it to the right read skill — /wiki-graph (structural traversal), /wiki-trace (claim provenance), /wiki-summarize (topic-coverage audit), or /wiki-query (prose synthesis, the default). Use when the user runs /wiki-ask, or asks a read question without naming which skill they want ("what's near X", "how does X connect to Y", "where did this claim come from", "what does the wiki know about X", "explain X"). For a specific operation, invoke that skill directly; /wiki-ask is the router that picks for you.
argument-hint: "<natural-language question>"
---

# wiki-ask

Single user-facing entry point for read-side queries. Classifies the user's plain-English input against the read-side skills and dispatches to the right one, surfacing the routing decision so it's auditable and overrideable. The router itself does no retrieval or synthesis — it picks a target and hands off.

Dispatch targets:
- **`/wiki-graph`** — structural traversal (`path` / `neighbors` / `subgraph`). Deterministic stdlib, ~no token cost.
- **`/wiki-trace`** — claim provenance: a *specific* quote/claim/anchor → its raw-vault source span. Deterministic stdlib, ~no token cost.
- **`/wiki-summarize`** — topic-coverage audit (five-section report). Always dispatches the librarian.
- **`/wiki-query`** — prose-synthesis Q&A. The fall-through default for vague or open-ended reads.

*Architecturally: inline tool-orchestration. No subagent dispatch — the router runs inline and invokes the target skill via the Skill tool.*

## Inputs

- **`$1` / the query** — the user's plain-English read request. Required; if empty, ask what they want to know.
- **Session context** — the wiki root (located as in the other read skills).

## Workflow

### 1. Validate input
If no query was given, ask the user what they want to know and stop. Don't route an empty string.

### 2. Locate the wiki
Find the wiki root in the current directory or upward (look for `wiki/CLAUDE.md`). Same as `/wiki-query`, `/wiki-graph`, `/wiki-summarize`.

### 3. Classify intent
Match the query against these patterns, in order. The first match wins; nothing matches → fall through to `/wiki-query`.

| Intent | Trigger phrasings | Route to |
|---|---|---|
| **Path** | "how does X connect to Y", "path between X and Y", "what links X and Y" | `/wiki-graph path X Y` |
| **Neighborhood** | "what's near X", "neighbors of X", "N-hop around X" | `/wiki-graph neighbors X [--depth N]` (default 1; explicit "N-hop" overrides) |
| **Subgraph** | "subgraph around X", "cluster around X", "everything connected to X" | `/wiki-graph subgraph X` |
| **Provenance** | "where did this claim come from", "trace this quote", "source span for '<quote>'", or a `[[page-slug#claim-id]]` anchor — keyed on a *specific* quote/claim/anchor to locate | `/wiki-trace "<quote \| [[page-slug#claim-id]]>"` |
| **Coverage** | "summarize X", "what does the wiki know about X", "coverage of X" | `/wiki-summarize X` |
| **Default** | a prose-synthesis question, or anything that matches nothing above | `/wiki-query <question>` |

> **Provenance vs. Coverage — the discriminator is specificity.** Route to `/wiki-trace` only when the request names a *specific* claim, quoted span, or `[[page#claim-id]]` anchor to locate. A *topic-level* provenance question ("what sources cover X", "where does the wiki's info on X come from" with no specific quote) is **not** a trace — it's Coverage or Default, because trace resolves a single span, not a topic's source set. Extract the quote/anchor from the natural-language request and pass it verbatim to `/wiki-trace`.

### 4. Resolve slugs + apply the three-class ambiguity policy
Read queries are cheap and reversible, so the HITL bar is deliberately lower than for write operations — default-and-surface rather than interrogate. Handle ambiguity by *kind*:

- **Class A — slug ambiguity** (e.g. "Karpathy" matches both `andrej-karpathy` and `karpathy-llm-wiki-pattern`): **ask** a single bounded multiple-choice clarification before dispatching, and count it (`routing_clarifications += 1`). Silent wrong-slug dispatch is invisible failure — the one case worth an interruption.
- **Class B — intent ambiguity** (e.g. "how does X connect to Y" — graph traversal or prose synthesis?): **do NOT ask.** Asking exposes the implementation distinction the router exists to hide. Default to the cheap dispatch (graph: ~1 s stdlib vs ~30K librarian tokens) and surface the escape line so the user can opt up:
  > For a synthesized explanation instead, re-ask: `/wiki-query 'explain how X connects to Y'`

  The cheap result is itself useful even when the more expensive dispatch was wanted.
- **Class C — genuinely vague** (e.g. "tell me about X"): **fall through** to `/wiki-query`, no question. The librarian's general-purpose synthesis is the safe default.

Soft cap: **at most one** clarifying question per invocation. Never chain.

### 5. Announce the routing
Always surface the translation inline before dispatching, so the decision is auditable and the user can override:

> Routed to: `/wiki-graph neighbors lethal-trifecta --depth 1`

### 6. Dispatch to the target skill
Invoke the chosen skill via the **Skill tool** with the translated arguments (the established chaining mechanism — same shape as `/wiki-capture` → `/wiki-ingest` and `/wiki-lint` → `/wiki-overview`). The target skill owns its own work and writes its own telemetry row.

Because the target runs inline in this same session, it knows it was reached via the router — its telemetry step adds `--routed-from ask` to its `append_query_log.py` call. Do **not** invoke the underlying script directly; go through the skill so its own rules and logging apply.

**Graph-route fall-through (slug not found).** A structural route (`path` / `neighbors` / `subgraph`) can name a concept the wiki covers *inside* another page rather than as a standalone slug (e.g. `prompt-injection`, which lives in `lethal-trifecta` + the security pages but has no page of its own). `graph_query.py` then exits 2 with `error: slug(s) not found in wiki: <slug>` and `/wiki-graph` returns that as a hard error. When a graph dispatch fails this way, **re-route — don't surface the error.** Fall through to `/wiki-query` with the *original* natural-language query (the slug-doesn't-resolve case is exactly where fuzzy prose retrieval beats graph traversal), surfacing a one-liner first:

> `prompt-injection` isn't a standalone page — routed to `/wiki-query` instead.

Then dispatch `/wiki-query <original question>` via the Skill tool as in the normal flow. This re-route is **router-only**: a *direct* `/wiki-graph` call is an explicit structural request, so erroring there stays the honest response — the `/wiki-graph` skill is unchanged. The re-route fires only on the slug-not-found signal (exit 2 / "slug(s) not found"); any other `/wiki-graph` failure is surfaced normally. Only one re-route per invocation — `/wiki-query` is the terminal fall-through and never bounces onward.

**Trace is a clean terminal route — no fall-through.** Unlike the graph case, `/wiki-trace` never hard-errors: a synthesized claim with no verbatim span returns a valid "None / it's synthesized — see the page's `sources:`" answer, not an error. So a trace dispatch is terminal; do **not** re-route it to `/wiki-query`. If the user wanted a synthesized explanation of *how* the claim was derived rather than its source span, surface the Class-B escape line (`For a synthesized explanation instead, re-ask: /wiki-query '...'`) — but the trace answer stands on its own.

### 7. Log the router telemetry row
After dispatch, write one router row to `wiki/.meta/query_log.jsonl` recording the classification decision:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_query_log.py" --wiki <wiki> \
  --query-skill ask \
  --query "<raw user query>" \
  --routed-to <query|graph|summarize|trace> \
  --routing-clarifications <N> \
  --duration-ms <router_wall_clock_ms>
git -C <wiki> add .meta/query_log.jsonl && git -C <wiki> commit -m "ask: telemetry"
```

Commit the row on its own (never `git add .`). The dispatched skill commits its own row separately.

Notes:
- `--query-skill ask` distinguishes the router row from the dispatched skill's row.
- `--query` is the user's raw plain-English input (not the translated subcommand).
- `--routed-to` is the dispatch target — required. If a graph route fell through to `/wiki-query` on a slug-not-found (step 6), record the *final* destination (`query`), not the failed graph attempt — the classification telemetry reflects where the query actually went. v1.1 leaves the initial mis-route implicit (no `reroute` field) to avoid a schema add; the count of these is recoverable later by cross-referencing `query` text against the absent slug if needed.
- `--routing-clarifications` is the count of clarifying questions asked this invocation (0 unless a Class A slug ambiguity fired).
- `search_mode` and all cost fields are null — the router does no retrieval or model work; the dispatched skill's row carries that skill's cost.
- `--duration-ms` is the router's own wall-clock (classification + dispatch handoff), not the dispatched skill's.

The result is two rows per `/wiki-ask` call: the router row (classification telemetry, for tuning the classifier) and the dispatched skill's normal row tagged `routed_from: ask` (operation telemetry, for per-skill cost analysis). Direct invocations of the underlying skills write only their own row.

**Exception — trace routes write only the router row.** `/wiki-trace`'s own `query_log` telemetry is still deferred (it's deterministic stdlib with no model/librarian cost to track — see the wiki-trace skill's telemetry rule), so a `routed_to: trace` call produces just the one router row. That's sufficient: the router row carries the classification signal this route exists to provide, and there is no per-skill cost to analyse. Wire trace's own dispatched row only if/when trace gains a measurable cost.

## Rules

- **Read-only.** The router never writes wiki pages and never dispatches a write skill.
- **Always surface the translation.** No silent routing — the user must be able to see and override the dispatch.
- **One clarifying question max.** Only on Class A slug ambiguity; never on intent ambiguity (Class B) or vagueness (Class C); never chain.
- **Default to the cheap dispatch on intent ambiguity, with an escape line.** The cheap result plus an opt-up beats an interruption that exposes the architecture.
- **Fall through to `/wiki-query` when nothing matches.** It's the general-purpose default; don't force a structural skill on a prose question.
- **Route to `/wiki-trace` only on a specific claim/quote/anchor.** Provenance of a *named* span → trace; a *topic-level* "what sources cover X" → Coverage/Default. Trace is a clean terminal route (its None/synthesized result is a valid answer, not an error) — never re-route it onward.
- **Re-route a failed graph dispatch to `/wiki-query`, don't error.** When a `path`/`neighbors`/`subgraph` route hits `slug(s) not found` (exit 2), the named concept lives inside another page rather than as its own slug — fuzzy prose retrieval answers it; graph traversal can't. Surface the one-liner and re-dispatch the original query. Router-only; direct `/wiki-graph` still errors. `routed_to` records the final destination.
- **Dispatch through the skill, not the script.** Go through `/wiki-graph` / `/wiki-summarize` / `/wiki-query` so their own rules and telemetry apply; the dispatched skill tags its row `routed_from: ask`.
- **Classifier-tuning signal.** `routing_clarifications` is telemetry: if its average climbs above ~0.3 per query, the classifier needs tuning, not the user. Don't paper over a bad classification with more questions.
- **No edits to `log.md`.** Read operations have no `## [<timestamp>]` log entry — `query_log.jsonl` is the audit trail. Same as the other read skills.
