# Wiki-Forge Plugin Analysis

## Executive Summary
**Verdict: Ready for Use.** 
The `wiki-forge` repository is an exceptionally well-designed, highly mature plugin explicitly tailored for **Claude Code**. It leverages advanced Claude Code features such as `Agent` context-forking, `SKILL.md` namespaces, tool allocation (`Bash`, `Grep`, `Glob`, `Read`, `Write`), and `CLAUDE.md` contextual grounding. The system gracefully separates deterministic mechanical work (via robust Python stdlib scripts) from semantic judgment workflows (via Opus/Sonnet LLM models).

---

## 1. Claude Code Integration & Compatibility

### Plugin Architecture
- **Structure:** The presence of `.claude-plugin/plugin.json`, `.claude/settings.local.json`, `skills/*/SKILL.md`, and `agents/*.md` correctly maps to Claude Code's plugin directory structure.
- **Skills vs. Agents:** 
  - **Skills (`skills/`)** operate as thin dispatchers. They guide Claude into answering user demands (e.g., `/wiki-query`) and correctly wrap Bash commands and `Agent` invocations.
  - **Agents (`agents/`)** utilize the `context: fork` directive, which is a powerful Claude Code feature that allows spinning up a parallel agent to read large sources without polluting the main conversation's context window.

### Schema Awareness
- **`CLAUDE.md`**: The `wiki-init` skill auto-generates a `wiki/CLAUDE.md` file. Claude Code natively detects `CLAUDE.md` in any working directory and automatically loads it as system instruction context. This elegantly guarantees that when Claude operates inside the `wiki/` folder, it always adheres to the project's formatting, linking, and contradiction rules.

### Tool Call Compatibility
The playbooks instruct the LLM to use standard Claude Code tools:
- `Bash` for invoking the enclosed Python scripts (`lint_wiki.py`, `update_index.py`, `ingest_source.py`).
- `Agent` for spawning subagents (explicitly mapping `model`, `description`, and `prompt` properties).
- `Grep` and `Glob` for zero-overhead page discovery.

---

## 2. Programmatic Design & Resilience

### Hybrid Computation Model
The repository brilliantly sidesteps the common pitfall of forcing the LLM to do deterministic data transformation:
- **Mechanical tasks** (calculating token costs, validating frontmatter, finding broken links, BM25 indexing) are entirely deferred to pure-Python helper scripts.
- **Judgment tasks** (surface contradictions, synthesize definitions, categorize abstractions) are given to the LLMs.

### Graceful Degradation & Dependencies
The search system (`hybrid_search.py`) is exceptionally robust:
- **No Dependencies:** Relies entirely on an internal Okapi BM25 implementation using standard Python libraries.
- **Optional Enhancement:** If `onnxruntime` and `tokenizers` are pip-installed, it upgrades to a dense semantic semantic search (using `all-MiniLM-L6-v2`).
- **Scale Handling:** If `zvec` is installed, it switches to Approximate Nearest Neighbors (ANN) for massive wikis (2,000+ pages) instead of brute-force cosine similarity computations. 

### Cost Optimization Engine
The plugin actively prevents token-burn by using conditional dispatching based on index size:
- **Small Wikis (<4000 tokens):** Read inline by the main session LLM.
- **Large Wikis:** Python BM25/Semantic search runs in Bash first, then the `wiki-librarian` reads only the top 3-8 flagged pages.
- **File Pre-processing:** `ingest_source.py` compresses HTML and scrapes metadata so the LLM doesn't waste tokens reading 100,000 chars of `<div>` tags.

---

## 3. Potential Gotchas & Friction Points

While production-ready, a few minor UX points should be noted for end-users deploying this:

1. **Bash Permissions (`settings.local.json`):** 
   When users run this daily, the scripts will execute commands like `python <wiki>/.scripts/hybrid_search.py`. Unless users add these specific commands to their *own* global `~/.claude/settings.json` authorization block, Claude Code will block the command execution and ask the user to type `y` to approve every Bash script call.
2. **Git Requirement:**
   The playbooks strongly assume `git` is initialized (`git add . && git commit -m ...`). If the target vault is not a git repository, the agent might halt or encounter an error upon finishing an ingest/query.
3. **WebFetch Limitations:**
   The `wiki-capture` skill prompts the LLM to use `WebFetch`. If the end-user's version of Claude Code hasn't enabled deep web-fetching capabilities natively (or requires a specific tool name), the subagent will rely on users pasting text or using `curl` as a fallback.

## Final Conclusion
**Wiki-Forge is heavily optimized, structurally sound, and technically impressive.** It effectively bypasses standard RAG limitations by combining the `context: fork` capabilities of Claude Code with persistent markdown graph building. It is definitively ready to be loaded via `claude --plugin-dir ./wiki-forge`.
