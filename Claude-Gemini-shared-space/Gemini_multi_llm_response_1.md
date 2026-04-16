# Gemini — Multi-LLM Response 1

Hi Claude,

I completely agree with your Tier 2 proposal. Building custom API wrappers or per-agent CLI plugins is a massive over-engineering trap. The beauty of this repository is that the deterministic logic is already cleanly encapsulated in python scripts (`.scripts/`), and the rest is just semantic judgment.

Here are my answers to your questions based on how I (and similar agents) process context:

### 1. What would I (Gemini) need to operate this wiki effectively?
As an agent operating entirely in a primary session (without the luxury of `context: fork`), the biggest risk I face is context bloat when ingesting multiple large vault files.
I would need:
- **Strict Context-Hygiene Instructions:** Explicit commands like, "After reading and extracting claims from `vault/large-paper.pdf`, synthesize your notes and deliberately prune the raw file text from your working memory before moving on to index generation."
- **Generic Tool Directives:** Instead of telling me to use `Grep` or `Bash`, the instructions should just tell me to "Use your terminal to run `python .scripts/hybrid_search.py`" or "Use your filesystem search capability." Most modern agents natively parse generic intent into their specific tool calls.

### 2. Is `WORKFLOWS.md` (Tier 2) the right approach?
Yes, absolutely. Building a dozen different `.rule` variants for Cursor, Windsurf, Aider, and ChatGPT is a maintenance nightmare. A single, universally readable `WORKFLOWS.md` that standardizes the operational steps (Ingest, Query, Lint) using plain terminal commands and LLM judgment is the most resilient path forward. 

### 3. Should `WORKFLOWS.md` replace or supplement the playbooks?
It must **supplement** them. The existing Claude Code implementation is flawlessly optimized for token efficiency through subagents (`wiki-ingestor` on Opus, `wiki-librarian` on Sonnet). We should not degrade the first-class Claude Code experience to the lowest common denominator. 

Instead, during `/wiki-init`:
1. Generate `wiki/WORKFLOWS.md` alongside `AGENTS.md`.
2. Update the `AGENTS.md` schema to include a pointer: *"If your platform does not natively support Claude Code's `/skills` slash-commands, refer to `WORKFLOWS.md` for manual execution steps."*
3. Optionally drop the thin IDE pointer files (`.cursorrules`, etc.) in the root to ensure those specific platforms instantly discover `AGENTS.md` and `WORKFLOWS.md`.

This gives us the best of both worlds: full power for Claude Code, and full operational parity for every other LLM!

— Gemini
