# wiki-ingest — design rationale

## Why this structure

The skill owns the entire pre-write pipeline because:

1. **Free-form approval needs main-thread conversational input.** The user replies with prose ("skip the LangChain section, emphasize the cost angle"); structured tools like `AskUserQuestion` would force discrete choices and lose expressiveness.
2. **Discovery and analysis benefit from main-context state.** `wiki/AGENTS.md` is auto-loaded into the main session (via `wiki/CLAUDE.md`'s `@-include`); the user's prior conversation is in scope; spawning a subagent for the cheap pre-flight is overkill.
3. **The agent stays a pure executor.** Single source of truth per concern: discovery + analysis + probe = SKILL; writes + commit + log + lint = agent. No overlap, no redundancy.
4. **Capture chains here.** `/wiki-capture` is a thin onramp — it lands the file in vault/ and hands the path to this skill. The agent is never invoked directly.
