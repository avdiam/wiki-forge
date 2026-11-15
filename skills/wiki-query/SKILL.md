---
name: wiki-query
description: Answer a question using the wiki's compiled pages, with citations, and optionally save the synthesized answer as a permanent synthesis page. Runs search in the main session and conditionally dispatches to the wiki-librarian subagent for large wikis. Small wikis are handled inline. Use when the user runs /wiki-query, asks "what do we know about X", or poses any research question to the wiki.
argument-hint: "<question>"
---

# wiki-query (smart dispatcher)

*Architecturally: inline orchestration with Agent-tool dispatch at step 3 for large wikis; small wikis stay inline.*

> Direct invocation. For a natural-language entry point that auto-routes between this, `/wiki-graph`, and `/wiki-summarize`, see `/wiki-ask`.

Handles query answering with conditional dispatch. Runs page discovery
(search or index reading) in the main session to minimize subagent cost.

## Workflow

### 1. Validate input

Expect a natural-language question. If empty, ask what the user wants to know.

### 2. Locate the wiki

- Find `wiki/CLAUDE.md` in the current directory or upward
- If none: tell the user, suggest `/wiki-init`

### 3. Check wiki size and decide strategy

**Before** branching to Path A or Path B, note the main-session wall-clock
start time. Path A's telemetry row needs it (no subagent, no `<usage>`
block); Path B's duration comes from `<usage>` or the subagent transcript.

Run the threshold check:

```bash
PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/hybrid_search.py" <wiki> --check-threshold
```

This prints `INDEX_READABLE <tokens> <pages>` or `SEARCH_NEEDED <tokens> <pages>`.

---

#### Path A: INDEX_READABLE (small wiki, <6000 index tokens)

**Handle everything inline — no subagent dispatch.**

1. Read the master `pages/indexes/index.md` to see which sub-indices exist.
2. Read the sub-indices most relevant to the question (e.g.,
   `pages/indexes/index-concepts.md` for a conceptual question).
3. Select 3–8 relevant pages based on title, summary, slug, tags.
4. Read the selected pages in full.
5. Synthesize an answer following the rules below.
6. Offer to file as a synthesis page.

This avoids the token cost of spawning a subagent for small wikis.

---

#### Path B: SEARCH_NEEDED (large wiki, >=6000 index tokens)

**Generate query variations, run search in the main session, then dispatch librarian with results.**

1. **Generate 3 paraphrase variations of the user's question** (Phase 2 — multi-query expansion). Produce them inline in this conversation as a JSON array. The goal is to widen recall against pages that use different vocabulary than the user's exact phrasing. Use these three angles, exactly one variation each:

   - **Paraphrase**: rewrite the question with different sentence structure but the same intent.
   - **Alternate vocabulary**: substitute synonyms or domain-equivalent terms (e.g., "kv-cache" ↔ "prompt caching"; "harness" ↔ "agent runtime").
   - **Acronym/expansion swap**: if the question contains an acronym, expand it; if it spells a term out, contract to the acronym (e.g., "RAG" ↔ "retrieval-augmented generation").

   Skip a variation only if the user's question doesn't admit it (e.g., no acronyms present → omit the third variation; pass N=2 or N=3 to the script). Don't pad with low-quality rephrasings — fewer good variations beat more weak ones.

2. **Run hybrid search** with the original question plus variations as a JSON array. Use a quoted heredoc so the JSON survives shell quoting unmolested even if the user's question contains apostrophes, double-quotes, dollar signs, or backticks:
   ```bash
   PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/hybrid_search.py" <wiki> --queries-json - <<'JSON'
   ["<original question>","<variation 1>","<variation 2>","<variation 3>"]
   JSON
   ```
   The script runs BM25+dense per query and RRF-fuses all 2N rank lists. The first array element is treated as canonical; the rest surface in `query_variations` in the output and in telemetry. With N=1 (single-element array), behavior is bit-identical to Phase 1. Remember to JSON-escape internal double-quotes within strings (`\"`).

3. Parse the JSON output. Check the top result's `score` (normalized
   0-1 against theoretical max — a score of 0.3 means the best result
   is only 30% as relevant as a perfect match). The `0.3` gate stays calibrated regardless of N because the script's RRF averages contributions across variations.

**If top score >= 0.3** (good search results):

Use the Agent tool to spawn the wiki-librarian:
- **subagent_type:** `"wiki-forge:wiki-librarian"`
- **description:** `"Wiki query — <short question summary>"`
- **prompt:**

```text
Context for this run:
- Question: <the question>
- Wiki root: <absolute path to wiki/>

Pages to read (from hybrid search — do NOT do your own search):
- <path 1> — <title 1>
  Snippet: <search snippet showing matching context>
- <path 2> — <title 2>
  Snippet: <snippet>
- <path 3> — <title 3>
  Snippet: <snippet>

Read these specific pages in full, synthesize an answer with [[wikilink]]
citations and confidence notes, and offer to save as a synthesis page.
```

**If top score < 0.3** (weak search results — possible vocabulary mismatch):

Use the Agent tool to spawn the wiki-librarian in fallback mode:
- **subagent_type:** `"wiki-forge:wiki-librarian"`
- **description:** `"Wiki query (fallback) — <short question summary>"`
- **prompt:**

```text
Context for this run:
- Question: <the question>
- Wiki root: <absolute path to wiki/>

Fallback mode: sub-index
The hybrid search returned low-confidence results. Read the most relevant
sub-index file(s) (e.g., pages/indexes/index-concepts.md for conceptual questions) to
find additional pages. Then read selected pages, synthesize, and offer
to file.
```

### 4. Synthesis rules (for Path A inline handling)

When synthesizing inline (small wiki), follow these rules:

- **Cite every factual claim** with a `[[wikilink]]` to the page.
- **Surface contradictions.** If two pages disagree, say so and cite both.
- **Flag confidence.** Note any page with confidence < 0.5 or `stale: true`.
- **Be concise.** 3–6 short paragraphs.
- **Don't invent.** Never answer from training knowledge or web-search results — full stop, regardless of how confident you feel.
- **Coverage-gap protocol — MUST follow when the wiki doesn't cover the question:**
  1. Say so explicitly.
  2. Offer the capture onramp: *"Do you have a source on this?"* — if yes, suggest `/wiki-capture <source>`.
  3. Offer the web-search onramp: *"Want me to search for authoritative sources you could capture?"* — if yes, run a web search via the host harness's search tool (e.g., `WebSearch` in Claude Code; whatever equivalent the host exposes elsewhere). Bias toward official documentation, primary sources, academic papers, and established publications. Return 3–5 candidate links with titles and one-line descriptions. **Do not synthesize an answer from the search results** — the user evaluates and captures what's worth ingesting. Avoid forums, anonymous blogs, and unattributed content. If no web-search tool is available in the host harness, say so and stop.
  4. Both onramps are mandatory — surface both, do not pick one.
- **Don't file trivial answers.** Only offer to file substantive
  multi-source synthesis. If the answer draws from fewer than 2 pages
  or is a simple factual lookup, skip the filing offer.

### 5. Offer to file (all paths)

After presenting the answer, ask:

```text
Would you like me to file this answer as a synthesis page at
syntheses/<slug>.md? Synthesis pages are picked up by lint and query,
so good answers compound.
```

If yes (and handling inline):

1. Create `syntheses/<slug>.md` using the synthesis template.
2. Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/update_index.py" <wiki>`
3. If git repo, stage only what this filing touched (never `git add .`): `git -C <wiki> add pages/syntheses/<slug>.md pages/indexes/ .meta/categories-index.json .meta/index_stats.json && git -C <wiki> commit -m "query-filed: <slug>"`
4. Log: `python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" query-filed --title "..." --created "syntheses/<slug>" --commit "<hash>" --wiki <wiki>`

If the librarian was dispatched, relay the filing question to the user,
then resume the librarian (SendMessage to its agent ID) with the user's
reply **quoted verbatim and labeled with its origin**, e.g.
`User reply (verbatim, main-session turn): "yes, file it"`. A paraphrase
("the user approved filing") reads as agent-originated, and the librarian
correctly refuses to file on it (s136).

### 6. Close the operation (query_log row + telemetry commit)

Run this after step 5's filing decision, so the filed slug is known. One call
writes the `query_log.jsonl` row and commits it on its own as `query:
telemetry` (explicit path; never `git add .`, never an amend of a filing
commit). Schema: `append_query_log.py --help`.

**Path B (librarian dispatch).** Pipe the librarian's result verbatim. The
script parses `Pages consulted: [[…]]` and `Filed as:` (`Not filed` → null)
from the footer and the cost fields from `<usage>` plus the transcript:

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/close_operation.py" query --wiki <wiki> \
  --query "<raw query string>" --search-mode <hybrid|hybrid_zvec|bm25_only> \
  --top-score <top result's normalized score> \
  --query-variations '<JSON array of the variations beyond the canonical query, or omit if N=1>' \
  --agent-id <agent-id> <<'RESULT'
<the librarian's result, verbatim, including <usage>...</usage>>
RESULT
```

`--search-mode`, `--top-score` and the variations come from the
`hybrid_search.py` JSON in step 3.

**Path A (inline).** There is no agent result. Pass the fields yourself:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/close_operation.py" query --wiki <wiki> \
  --query "<raw query string>" --search-mode inline \
  --pages-consulted "<comma-sep slugs you read>" --filed-as "<synthesis-slug or ''>" \
  --duration-ms <main-session wall-clock from step 3>
```

If this invocation was routed from `/wiki-ask`, add `--routed-from ask`.
Telemetry is observational: report `warnings` briefly in the relay, but do
NOT retry and do NOT fail the query.

### 7. Relay

Return the answer to the user. Include a footer:

```text
Pages consulted: [[page-1]], [[page-2]], [[page-3]]
Filed as: syntheses/<slug>.md   (or: Not filed)
```

## Rules

- Small wikis: handle inline, no subagent
- Large wikis: run search in main session, dispatch librarian with results
- The search script call is cheap (bash command) — always run it in main session
- Librarian runs on Sonnet (cheaper; reading + synthesis is easier than ingestion)
- If the subagent is unavailable, fall back to inline handling regardless of size
