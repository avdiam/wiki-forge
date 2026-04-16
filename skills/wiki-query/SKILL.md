---
name: wiki-query
description: Answer a question using the wiki's compiled pages, with citations, and optionally save the synthesized answer as a permanent synthesis page. Runs search in the main session and conditionally dispatches to the wiki-librarian subagent for large wikis. Small wikis are handled inline. Use when the user runs /wiki-query, asks "what do we know about X", or poses any research question to the wiki.
---

# wiki-query (smart dispatcher)

Handles query answering with conditional dispatch. Runs page discovery
(search or index reading) in the main session to minimize subagent cost.

## Workflow

### 1. Validate input

Expect a natural-language question. If empty, ask what the user wants to know.

### 2. Locate the wiki

- Find `wiki/CLAUDE.md` in the current directory or upward
- If none: tell the user, suggest `/wiki-init`

### 3. Check wiki size and decide strategy

Run the threshold check:

```bash
python <wiki>/.scripts/hybrid_search.py <wiki> --check-threshold
```

This prints `INDEX_READABLE <tokens> <pages>` or `SEARCH_NEEDED <tokens> <pages>`.

---

#### Path A: INDEX_READABLE (small wiki, <4000 index tokens)

**Handle everything inline — no subagent dispatch.**

1. Read the master `indexes/index.md` to see which sub-indices exist.
2. Read the sub-indices most relevant to the question (e.g.,
   `indexes/index-concepts.md` for a conceptual question).
3. Select 3–8 relevant pages based on title, summary, slug, tags.
4. Read the selected pages in full.
5. Synthesize an answer following the rules below.
6. Offer to file as a synthesis page.

This avoids the token cost of spawning a subagent for small wikis.

---

#### Path B: SEARCH_NEEDED (large wiki, >=4000 index tokens)

**Run search in the main session, then dispatch librarian with results.**

1. Run hybrid search:
   ```bash
   python <wiki>/.scripts/hybrid_search.py <wiki> "<question>"
   ```
2. Parse the JSON output. Check the top result's `score` (normalized
   0-1 against theoretical max — a score of 0.3 means the best result
   is only 30% as relevant as a perfect match).

**If top score >= 0.3** (good search results):

Use the Agent tool to spawn the wiki-librarian:
- **model:** `"sonnet"`
- **description:** `"Wiki query — <short question summary>"`
- **prompt:**

```text
You are the wiki-librarian subagent. Find your full playbook by running
Glob for '**/agents/wiki-librarian.md' and read it in full before starting.

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
- **model:** `"sonnet"`
- **description:** `"Wiki query (fallback) — <short question summary>"`
- **prompt:**

```text
You are the wiki-librarian subagent. Find your full playbook by running
Glob for '**/agents/wiki-librarian.md' and read it in full before starting.

Context for this run:
- Question: <the question>
- Wiki root: <absolute path to wiki/>

Fallback mode: sub-index
The hybrid search returned low-confidence results. Read the most relevant
sub-index file(s) (e.g., indexes/index-concepts.md for conceptual questions) to
find additional pages. Then read selected pages, synthesize, and offer
to file.
```

### 4. Synthesis rules (for Path A inline handling)

When synthesizing inline (small wiki), follow these rules:

- **Cite every factual claim** with a `[[wikilink]]` to the page.
- **Surface contradictions.** If two pages disagree, say so and cite both.
- **Flag confidence.** Note any page with confidence < 0.5 or `stale: true`.
- **Be concise.** 3–6 short paragraphs.
- **Don't invent.** If the wiki doesn't cover it, say so.
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
2. Run `python <wiki>/.scripts/update_index.py <wiki>`
3. If git repo: `git add . && git commit -m "query-filed: <slug>"`
4. Log: `python <wiki>/.scripts/append_log.py query-filed --title "..." --created "syntheses/<slug>" --commit "<hash>" --wiki <wiki>`

If the librarian was dispatched, relay the filing question and decision.

### 6. Relay

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
