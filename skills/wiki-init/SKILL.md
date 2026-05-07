---
name: wiki-init
description: Bootstrap a new LLM Wiki vault in the current directory. Creates the wiki/ and vault/ folder structure, installs CLAUDE.md (the schema) with custom domain categories, copies helper scripts to wiki/.scripts/, seeds indexes/log/overview, optionally sets up semantic search, and optionally sets up git. Use when the user runs /wiki-init or asks to "set up a new wiki", "start an LLM wiki", or "create a knowledge base" in a project.
disable-model-invocation: true
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

**Python interpreter identity check (Windows-specific footgun):**

If both `python3` and `python` commands resolve, verify they point at the
same interpreter. On Windows, `python3` often maps to the Microsoft Store
stub while `python` maps to a separately-installed runtime — both pass
the `>= 3.9` check, but `setup_search.py` installs packages via
`sys.executable`, so packages land under whichever command invoked it.
Later calls using the other command then fail with `ImportError`.

Run via Bash:

```bash
python3 -c "import sys; print(sys.executable)" 2>/dev/null
python  -c "import sys; print(sys.executable)" 2>/dev/null
```

- If only one command resolves: skip this check — there's no ambiguity.
- If both resolve to the **same** path: continue silently.
- If both resolve to **different** paths: **WARN** the user:

  > "Your `python3` and `python` commands resolve to different interpreters:
  > - `python3` → `<path-A>`
  > - `python`  → `<path-B>`
  >
  > `setup_search.py` installs packages under whichever command invokes
  > it — if you later call scripts via the other command, imports will
  > fail. Pick one and use it consistently throughout the wiki's lifetime."

  Ask: "Which command would you like me to use going forward — `python3`
  or `python`?" Record the choice and use it in every subsequent Bash
  invocation this session. Include it in the final report at step 8 so
  the user can reproduce it later.

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

**Git identity (required when using git):**

If Git is available and the user intends to use it, also verify commit
identity is configured. Without `user.name` and `user.email`, the first
commit in step 7 fails with *"Please tell me who you are"*.

Run via Bash:

```bash
git config user.name
git config user.email
```

- If both print values: continue.
- If either is missing: **STOP** before step 7. Tell the user:

  > "Git is installed but commit identity is not configured. Run:
  > - `git config --global user.name \"Your Name\"`
  > - `git config --global user.email \"you@example.com\"`
  >
  > (Or configure locally inside the wiki after init if you prefer
  > per-repo identity.) Once set, we can continue — or say the word and
  > I'll proceed without git tracking (step 7 will be skipped)."

  Wait for the user to configure or opt out before proceeding.

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

### 3. Install the Schema and Workflows

- Ask the user: *"What kind of knowledge base are you building? (e.g., AI Research, Cooking, Law, RPG Campaign)"*
- If they haven't provided this context, **wait for their answer**.
- Based on their intended topic, generate a set of 5-7 logical "seed
  categories" (kebab-case).
- **Find the schema template:** Run Glob for `**/wiki-init/templates/CLAUDE.md`
  to locate the template file. Read it in full.
- Using a find-and-replace, overwrite the default AI-centric "Seed
  categories" section with your newly generated custom categories.
- Write this customized schema to `wiki/CLAUDE.md`. The schema is
  loaded natively by Claude Code when working in `wiki/`.
- Also write the same content to `wiki/AGENTS.md` to ensure multi-tool awareness.
- **Find the workflows template:** Run Glob for `**/wiki-init/templates/WORKFLOWS.md` to locate it, then copy it verbatim to `wiki/WORKFLOWS.md` via Bash:

  ```bash
  cp <path-to-templates>/WORKFLOWS.md wiki/WORKFLOWS.md
  ```

  This file is a verbatim copy (no customization), so a single `cp` is faster and quieter than Read+Write. It provides universal LLM operational guidance.
- Add a prompt:
  > "Generate IDE pointer files (`.cursorrules`, `.windsurfrules`) in the project root? These help tools like Cursor and Windsurf discover the wiki schema automatically."
- Only generate if the user says yes. These files should contain exactly:

  ```markdown
  # Wiki Forge
  See `wiki/AGENTS.md` for full schema and structural rules.
  See `wiki/WORKFLOWS.md` for step-by-step operational instructions.
  Helper scripts are in `wiki/.scripts/` — run them via terminal.
  ```

### 4. Install helper scripts

**Find the scripts directory:** Run Glob for `**/wiki-init/templates/scripts/*.py`
to locate the templates folder. The scripts are verbatim copies — no
customization happens at install time — so use a single Bash `cp` rather
than Read+Write per file:

```bash
cp <path-to-templates>/scripts/*.py wiki/.scripts/
```

This is instant and silent compared to looping Read+Write across each
script (which produces visible "Writing X..." steps and burns tokens for
no benefit). Claude Code runs Git Bash on Windows per the project shell
contract, so `cp` works cross-platform; if a future PowerShell-only
environment ever needs to be supported, fall back to
`python -c "import shutil, glob, pathlib; [shutil.copy(p, 'wiki/.scripts/') for p in glob.glob('<path>/scripts/*.py')]"`.

The scripts installed:

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

**Reserve Read+Write for files that get customized at install** — currently
`CLAUDE.md` (seed categories find-and-replace) and `AGENTS.md` (mirror of
the customized CLAUDE.md). Verbatim files (every Python script,
`WORKFLOWS.md`) go through `cp`.

### 5. Optional: Semantic search setup

First, check if semantic search is already configured:

```bash
python wiki/.scripts/setup_search.py --check
```

- If output is `installed`: skip this step entirely. Note "semantic search:
  already configured" in the final report.
- If output is `not installed`: tell the user:

  > "Semantic search enables hybrid keyword+meaning search for larger wikis
  > (~90+ pages). It requires ~100MB of dependencies (onnxruntime, tokenizers,
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

Replace any `<DATE>` placeholder with today's ISO date (YYYY-MM-DD), and any `<TIME>` placeholder with the current wall-clock time (HH:MM, 24-hour). The init entry in `log.md` must match the `YYYY-MM-DD HH:MM` format every subsequent `append_log.py` call uses — otherwise `/wiki-log --since <date>` filtering and any awk-based log parsing mis-sort the leading entry.

### 7. Git setup

Ask: "Initialize git tracking for the wiki? (recommended — the subagents
commit after every operation)"

- **Yes, parent is a git repo** → stage `wiki/` in the existing repo
- **Yes, parent is not a git repo** → `git init` inside `wiki/`
- **No** → skip (but note: no audit trail for operations)

### 8. Report and next steps

Tell the user:

- Where the wiki was created (full path)
- The commands now available: `/wiki-capture`, `/wiki-ingest`,
  `/wiki-query`, `/wiki-lint`, `/wiki-log`, `/wiki-overview`, `/wiki-triage`, `/wiki-retract`
- The four subagents installed: `wiki-ingestor`, `wiki-librarian`, `wiki-linter`, `wiki-curator`
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
