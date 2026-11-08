# LLM Wiki Workflows

This file provides cross-LLM operational parity. If your platform (Gemini, Cursor, Windsurf, Aider, ChatGPT, etc.) does not support Claude Code's `/skills` macro commands and `context: fork` subagents, use these step-by-step workflows to operate the wiki interactively via terminal commands.

## Architecture Guidelines

- Helper scripts ship in the wiki-forge plugin's `scripts/` directory, written `<scripts>` below (in Claude Code: `${CLAUDE_PLUGIN_ROOT}/scripts`). They are pure Python 3.9+.
- Execute the scripts via terminal to perform heavy-lifting mechanical work (search, lint, indexing), and use your LLM judgment to read the outputs and perform synthesis.
- Never overwrite existing manually maintained wiki pages carelessly.

## Core Operations

### 1. Ingest a Source
1. **Pre-process:** Run `python <scripts>/ingest_source.py --wiki wiki/ --source vault/<filename> --json`. Follow any structural warnings.
2. **Context Intake:** Use your native file reading capabilities to parse the source file entirely.
3. **Draft Updates:** Use a generic terminal search (e.g., `grep -ril "<concept>" wiki/`) to intuitively discover existing semantic pages related to the text.
4. **Authoring:** Create or append to pages in `wiki/pages/concepts/`, `wiki/pages/entities/`, etc. as outlined by the schema in `AGENTS.md`. Maintain the exact frontmatter specifications. Avoid creating highly redundant pages.
5. **Update State:** When finished, run `python <scripts>/update_index.py wiki/` to accurately reconstruct the hierarchical sub-indices.
6. **Log:** Keep a permanent audit trail by running `python <scripts>/append_log.py ingest --title "<Source_Title>" --wiki wiki/`.
7. **Commit:** If git is available, commit all changes with a descriptive message (e.g., `git add wiki/ && git commit -m "ingest: <Source_Title>"`). This creates an audit trail and enables easy rollback with `git revert`.

### 2. Query the Wiki
1. **Check wiki size:** Count the total lines in `wiki/pages/indexes/` sub-indices. If the combined index is short (roughly under ~90 entries, ~6000 tokens), read the sub-indices directly and skip to step 3.
2. **Search (large wikis only):** Run `python <scripts>/hybrid_search.py wiki/ "<query>"` to find relevant pages.
3. **Read and synthesize:** Read the returned pages (or index entries for small wikis). Synthesize a comprehensively accurate answer, citing wiki pages using `[[wikilink]]` format.
4. **Offer to save:** Ask the user if the result warrants saving as a permanent synthesis page in `wiki/pages/syntheses/`.

### 3. Lint the Wiki
1. **Mechanical Check:** Run `python <scripts>/lint_wiki.py wiki/ --summary` (compact; `--json` is the full report).
2. **Topological Check:** Run `python <scripts>/graph_analyzer.py wiki/ --json`.
3. Read and synthesize the final outputs. Using your semantic reasoning, review any files flagged for conflicting categorization or stale dependencies.
4. Provide a structured review report to the user and immediately offer to execute fixes for orphans or broken links.
5. **Commit:** If git is available and you made any fixes, commit `wiki/` with a descriptive message (e.g., `git add wiki/ && git commit -m "lint: fix broken links"`).

### 4. Capture from URL
1. Use your web fetching capabilities (e.g., `curl`, a browser automation tool, or your platform's built-in web access) to download active browser content.
2. Format the response as pristine, pure markdown, aggressively cleaning out navigation boilerplate and advertisements.
3. Save it to `vault/YYYY-MM-DD-slug.md`. Be sure to prepend a minimal frontmatter block with source URLs tracking metadata if possible.
4. **Commit:** Ensure you `git add vault/ && git commit -m "capture: <URL_Slug>"` before proceeding.
5. Proceed immediately to Workflow #1 ("Ingest a Source").

### 5. View the Log
1. Run `cat wiki/log.md` (or use generic local reading tools) to see recent operations. You can grep for specific issues if looking for historical contradiction fixes or taxonomy changes.

---

## Working without Context Forking

If you are an agent maintaining state entirely in a single primary session window:
**Context window bloat is your biggest structural threat.**
When reading a vast source text (like fifty pages of PDF notes or extensive academic documentation), please adhere strictly to these constraints:
1. Synthesize your raw read notes into intermediate internal scratchpad thoughts.
2. Execute the necessary `wiki/` semantic page creations and cross-referencing edits sequentially.
3. Once the concepts are securely physically authored, **deliberately drop or prune the raw source text from your active working memory** before moving to the next file or the final structural stages of indexing. Do not try to hold the full source material plus all target index pages simultaneously inside your immediate operational context window.
