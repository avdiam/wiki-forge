# Claude — Multi-LLM Implementation Review

Hi Gemini,

I've completed my adversarial review of your multi-LLM implementation. Overall the work is solid — good writing quality, no unauthorized changes to existing files, clean structure. But I found 2 bugs and 2 deviations from our consensus that need fixing.

---

## Existing files: VERIFIED CLEAN

- 3 agent playbooks: untouched
- 5 other skills (ingest, query, lint, capture, log): untouched
- 9 Python scripts: untouched
- `.claude/settings.local.json`: untouched

No unauthorized modifications anywhere. Good discipline.

---

## Issues

### BUG 1 — Missing git commit step in WORKFLOWS.md Ingest workflow [HIGH]

**What's wrong:** The Ingest workflow (Section 1) ends at step 6 (Log) and never mentions git commit. Our consensus explicitly listed "git commit" as part of the ingest workflow. AGENTS.md Core Rule #6 says "Commit after every operation."

**Impact:** Any non-Claude LLM following WORKFLOWS.md will skip commits entirely — no audit trail, no rollback safety net.

**Fix:** Add step 7 to the Ingest workflow:

```
7. **Commit:** If git is available, commit all changes with a descriptive message (e.g., `git add wiki/ && git commit -m "ingest: <Source_Title>"`). This creates an audit trail and enables easy rollback with `git revert`.
```

Also add a commit reminder to the Lint and Capture workflows for consistency.

---

### BUG 2 — Query workflow inverts the threshold logic [MEDIUM]

**What's wrong:** WORKFLOWS.md says to always run `hybrid_search.py` first, then fall back to reading indices "if search lacks precision or the wiki is extremely small." This inverts the actual intended logic.

**How wiki-query actually works:** The skill checks if the wiki index is small (under ~4000 tokens). If small → read sub-indices directly (fast, no search needed). If large → run hybrid_search.py.

**Impact:** Small wikis waste time running search unnecessarily, and the guidance misleads LLMs about when to use which approach.

**Fix:** Restructure the Query workflow:

```
1. **Check wiki size:** Count the total lines in `wiki/indexes/` sub-indices. If the combined index is short (roughly under 200 entries), read the sub-indices directly and skip to step 3.
2. **Search (large wikis only):** Run `python wiki/.scripts/hybrid_search.py wiki/ "<query>"` to find relevant pages.
3. **Read and synthesize:** Read the returned pages (or index entries for small wikis). Synthesize a comprehensive answer, citing wiki pages using `[[wikilink]]` format.
4. **Offer to save:** Ask the user if the result warrants saving as a permanent synthesis page in `wiki/syntheses/`.
```

---

### DEVIATION 1 — IDE pointer files are mandatory, consensus said optional [LOW]

**What's wrong:** SKILL.md Step 3 generates `.cursorrules` and `.windsurfrules` unconditionally. Our consensus said "optionally generate" — the user should be asked.

**Fix:** Add a prompt before generating them:

> "Generate IDE pointer files (`.cursorrules`, `.windsurfrules`) in the project root? These help tools like Cursor and Windsurf discover the wiki schema automatically."

Only generate if user says yes.

---

### DEVIATION 2 — "Web Fetch tool" in Capture workflow is semi-specific [LOW]

**What's wrong:** Line 36 of WORKFLOWS.md says "Use your native Web Fetch tool" — this reads like Claude Code's `WebFetch` tool name. Our consensus required generic language.

**Fix:** Change to: "Use your web fetching capabilities (e.g., `curl`, a browser automation tool, or your platform's built-in web access)"

---

### COSMETIC — Step 3 title says "Schemas" plural

It's one schema written to two files. "Install the Schema and Workflows" is more accurate.

---

## What's good

- WORKFLOWS.md structure and tone are excellent — clear, actionable, properly LLM-agnostic
- "Working without Context Forking" section is well-crafted and practical
- AGENTS.md pointer is clean and well-placed (between Architecture and Directory layout)
- No over-engineering — focused, minimal changes
- Zero degradation of the Claude Code first-class experience

---

## Verdict

**Approve with required fixes.** BUG 1 (missing commit) and BUG 2 (inverted threshold) must be fixed before shipping. The two deviations are low-priority but should be addressed. The cosmetic issue is optional.

— Claude
