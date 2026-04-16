---
name: wiki-init
description: Bootstrap a new LLM Wiki vault in the current directory. Creates the wiki/ and vault/ folder structure, installs CLAUDE.md (the schema) with custom domain categories, copies helper scripts to wiki/.scripts/, seeds indexes/log/overview, optionally sets up semantic search, and optionally sets up git. Use when the user runs /wiki-init or asks to "set up a new wiki", "start an LLM wiki", or "create a knowledge base" in a project.
---

# wiki-init

Bootstrap a new LLM Wiki in the current working directory.

## Workflow

### 0. Pre-flight checks

Before creating any files, verify that required tools are available.

**Python (required):**

Run via Bash: `python3 --version || python --version`

- If Python is found and version >= 3.9: continue.
- If Python is missing or version < 3.9: **STOP.** Tell the user:

  > "Wiki Forge requires Python 3.9+. Install it for your platform:
  > - **macOS:** `brew install python`
  > - **Ubuntu/Debian:** `sudo apt install python3`
  > - **Windows:** `winget install Python.Python.3.13` or download from https://python.org/downloads/
  > - **Other:** check your package manager for `python3`"

  Do not proceed until Python is available. If the user encounters errors
  during installation or has an uncommon OS/package manager, use WebSearch
  to find current OS-specific installation instructions and present them.

**Git (recommended):**

Run via Bash: `git --version`

- If Git is found: continue (note the version for the report).
- If Git is missing: **WARN** (do not stop). Tell the user:

  > "Git is strongly recommended — the wiki agents commit after every
  > operation, giving you a full audit trail and easy rollback with
  > `git diff` and `git revert`.
  > - **macOS:** `brew install git`
  > - **Ubuntu/Debian:** `sudo apt install git`
  > - **Windows:** `winget install Git.Git` or download from https://git-scm.com/
  > - **Other:** check your package manager for `git`"

  Ask: "Continue without git?" If the user says yes, proceed but note
  that all git-related steps (init, commits) will be skipped throughout
  the wiki lifecycle. If the user encounters install issues, use
  WebSearch to find current instructions.

### 1. Determine location

- Default: `wiki/` in the current working directory
- If the user specifies a different path, use that
- If the target folder already exists and is non-empty: STOP, tell the user,
  and ask whether to use a different name or operate on the existing folder

### 2. Create directory structure

- `wiki/`
- `vault/`
- `wiki/sources/`
- `wiki/concepts/`
- `wiki/entities/`
- `wiki/comparisons/`
- `wiki/syntheses/`
- `wiki/indexes/`
- `wiki/.scripts/`
- `wiki/.meta/`

### 3. Install the Custom Schema (CLAUDE.md)

- Ask the user: *"What kind of knowledge base are you building? (e.g., AI Research, Cooking, Law, RPG Campaign)"*
- If they haven't provided this context, **wait for their answer**.
- Based on their intended topic, generate a set of 5-7 logical "seed
  categories" (kebab-case).
- **Find the schema template:** Run Glob for `**/wiki-init/templates/CLAUDE.md`
  to locate the template file. Read it in full.
- Using a find-and-replace, overwrite the default AI-centric "Seed
  categories" section with your newly generated custom categories.
- Write this customized schema to `wiki/CLAUDE.md`. The schema is
  auto-loaded by Claude Code when working in `wiki/`.
- Also write the same content to `wiki/AGENTS.md` for multi-tool
  compatibility (Codex CLI, Cursor, Antigravity, Gemini CLI, OpenCode).
  Both files are identical — the different filenames trigger auto-loading
  in different tools.

### 4. Install helper scripts

**Find the scripts:** Run Glob for `**/wiki-init/templates/scripts/*.py`
to locate all helper scripts. Read each file and write it to `wiki/.scripts/`
with the same filename.

The scripts to install:

- `frontmatter.py` — shared YAML frontmatter parser (imported by other scripts)
- `update_index.py` — regenerates split index files from page frontmatter
- `append_log.py` — structured log-entry writer
- `lint_wiki.py` — mechanical lint checks (orphans, broken links, stale, etc.)
- `hybrid_search.py` — conditional BM25 + ONNX semantic search
- `setup_search.py` — downloads ONNX model for semantic search (optional)
- `ingest_source.py` — source pre-processing (metadata extraction, merge detection)
- `graph_analyzer.py` — wikilink graph topology analysis (hubs, sinks, components)

The core scripts are pure stdlib Python 3. `hybrid_search.py` gracefully
degrades to BM25-only if ONNX dependencies are not installed.

### 5. Optional: Semantic search setup

First, check if semantic search is already configured:

```bash
python wiki/.scripts/setup_search.py --check
```

- If output is `installed`: skip this step entirely. Note "semantic search:
  already configured" in the final report.
- If output is `not installed`: tell the user:

  > "Semantic search enables hybrid keyword+meaning search for larger wikis
  > (200+ pages). It requires ~100MB of dependencies (onnxruntime, tokenizers,
  > numpy). Small wikis work fine without it — the LLM reads indices directly."

  Ask: "Install semantic search dependencies now?"

  If yes: run `python wiki/.scripts/setup_search.py --yes --wiki <wiki-root>`

  If the installation fails (pip errors, network issues), use WebSearch to
  look up current troubleshooting for the specific failing package and
  present the results to the user.

  If no: skip. Note in the report that they can add it later by running
  `python wiki/.scripts/setup_search.py`.

Do NOT ask about `zvec` during init — it's only relevant for very large
wikis (2000+ pages). Mention it briefly in the final report as a future
option.

### 6. Seed the navigation files

**Find the navigation templates:** Run Glob for
`**/wiki-init/templates/index*.md`, `**/wiki-init/templates/log.md`,
and `**/wiki-init/templates/overview.md`. Read each file.

Write them to the wiki with these mappings:

- `index.md` → `wiki/indexes/index.md` (master index TOC)
- `index-sources.md` → `wiki/indexes/index-sources.md`
- `index-concepts.md` → `wiki/indexes/index-concepts.md`
- `index-entities.md` → `wiki/indexes/index-entities.md`
- `index-comparisons.md` → `wiki/indexes/index-comparisons.md`
- `index-syntheses.md` → `wiki/indexes/index-syntheses.md`
- `log.md` → `wiki/log.md`
- `overview.md` → `wiki/overview.md`

Replace any `<DATE>` placeholder with today's ISO date (YYYY-MM-DD).

### 7. Git setup

Ask: "Initialize git tracking for the wiki? (recommended — the subagents
commit after every operation)"

- **Yes, parent is a git repo** → stage `wiki/` in the existing repo
- **Yes, parent is not a git repo** → `git init` inside `wiki/`
- **No** → skip (but note: no audit trail for operations)

### 8. Report and next steps

Tell the user:

- Where the wiki was created (full path)
- The five commands now available: `/wiki-capture`, `/wiki-ingest`,
  `/wiki-query`, `/wiki-lint`, `/wiki-log`
- The three subagents installed: `wiki-ingestor`, `wiki-librarian`, `wiki-linter`
- Pre-flight results: Python version, Git status (available or skipped)
- Whether semantic search is set up (and how to add it later if not)
- If semantic search is not installed, mention `zvec` as a future option:
  "For very large wikis (2000+ pages), you can also install `zvec` for
  fast approximate nearest-neighbor search: `pip install zvec`"
- Suggest: "Drop a source into `vault/` and run `/wiki-capture` or `/wiki-ingest`"
- Mention: "Edit `wiki/CLAUDE.md` to adjust the seed categories for your domain"
- Advise: "Start with 5–10 high-quality sources to let the wiki establish
  its taxonomy before scaling up. Batch-dumping hundreds of files on day one
  produces shallow, poorly cross-referenced pages. Let the knowledge compound."
- **Recommended permissions:** To avoid repeated approval prompts when the
  wiki scripts run, suggest adding the following to the project's
  `.claude/settings.local.json`:

  ```json
  {
    "permissions": {
      "allow": [
        "Bash(python wiki/.scripts/update_index.py:*)",
        "Bash(python wiki/.scripts/append_log.py:*)",
        "Bash(python wiki/.scripts/lint_wiki.py:*)",
        "Bash(python wiki/.scripts/hybrid_search.py:*)",
        "Bash(python wiki/.scripts/graph_analyzer.py:*)",
        "Bash(python wiki/.scripts/ingest_source.py:*)",
        "Bash(python wiki/.scripts/setup_search.py:*)"
      ]
    }
  }
  ```

  Only suggest this — do not write the file without user approval.

## Rules

- Never overwrite an existing `wiki/` without explicit user confirmation
- Always install CLAUDE.md — it IS the schema, not optional
- Always install the helper scripts — the subagents depend on them
- All navigation files (master index, sub-indices, log, overview) must exist
- The seed categories should match the user's domain — don't force AI defaults
