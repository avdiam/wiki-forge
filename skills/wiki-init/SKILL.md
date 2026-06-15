---
name: wiki-init
description: Bootstrap a new LLM Wiki vault in the current directory. Creates the wiki/ and vault/ folder structure, installs the schema files (CLAUDE.md, AGENTS.md, README.md, CATEGORIES.md) with custom domain categories injected into CATEGORIES.md, seeds pages/indexes/log/overview, optionally sets up semantic search, and optionally sets up git. Helper scripts and subagents ship with the wiki-forge plugin and are NOT copied into the project (pure-consumption model). Pass --dual-claude to additionally install the wiki-side identity files and SessionStart hook for a dual-Claude (wiki + clipper) project shape. Use when the user runs /wiki-init or asks to "set up a new wiki", "start an LLM wiki", or "create a knowledge base" in a project.
argument-hint: "[--dual-claude]"
disable-model-invocation: true
---

# wiki-init

Bootstrap a new LLM Wiki in the current working directory.

## Argument parsing

Before step 0, parse `$ARGUMENTS` for flags:

- `--dual-claude` — install the dual-Claude bootstrap (wiki-side identity files + SessionStart hook). Adds optional step 7 between the seed step and git setup. Default off — `/wiki-init` produces a generic wiki-only install unless this flag is passed.

If the flag is unrecognized or the argument string is empty, proceed with defaults.

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
- `wiki/pages/sources/`
- `wiki/pages/concepts/`
- `wiki/pages/entities/`
- `wiki/pages/comparisons/`
- `wiki/pages/syntheses/`
- `wiki/pages/indexes/`
- `wiki/.meta/`

(No `wiki/.scripts/` — helper scripts ship with the wiki-forge plugin and run from `${CLAUDE_PLUGIN_ROOT}/scripts/`, not from inside the project.)

### 3. Install the Schema and Workflows

The schema lives across five files since the 2026-05-13 decomposition. Four are verbatim copies (no per-install customization); one is customized at install time.

**Verbatim files (use `cp`):**

- `templates/CLAUDE.md` → `wiki/CLAUDE.md` (1-line `@-include` pointer to AGENTS.md; Claude Code's auto-load anchor)
- `templates/AGENTS.md` → `wiki/AGENTS.md` (operational rules, frontmatter schema, page templates)
- `templates/README.md` → `wiki/README.md` (human orientation, architecture, directory layout)
- `templates/WORKFLOWS.md` → `wiki/WORKFLOWS.md` (manual-execution playbook for non-Claude-Code AI platforms)

Use a single Bash `cp` for all four — faster and quieter than Read+Write per file:

```bash
TEMPLATES=<path-to-templates>
cp $TEMPLATES/CLAUDE.md $TEMPLATES/AGENTS.md $TEMPLATES/README.md $TEMPLATES/WORKFLOWS.md wiki/
```

**Customized file (use Read+Write):** `templates/CATEGORIES.md` → `wiki/CATEGORIES.md`. This is the only schema file customized per install.

- Ask the user: *"What kind of knowledge base are you building? (e.g., AI Research, Cooking, Law, RPG Campaign)"*
- If they haven't provided this context, **wait for their answer**.
- Based on their intended topic, generate a set of 5-7 logical seed categories — kebab-case slugs plus one-line descriptions, matching the bullet shape in the template.
- **Find the template:** Run Glob for `**/wiki-init/templates/CATEGORIES.md` to locate it. Read it in full.
- Using a find-and-replace, overwrite the default AI-centric authoritative category list (the bullets between the `## Authoritative category list` header and the next `##` header) with your newly generated custom categories. **Preserve the parser-compatible format exactly:** `` - `<slug>` — <description> ``. `lint_wiki.py`'s `load_authoritative_categories` parses this section at runtime — any deviation from the heading or bullet shape breaks category enforcement.
- Write the customized content to `wiki/CATEGORIES.md`.

**IDE pointer files:**

- Add a prompt:
  > "Generate IDE pointer files (`.cursorrules`, `.windsurfrules`) in the project root? These help tools like Cursor and Windsurf discover the wiki schema automatically."
- Only generate if the user says yes. These files should contain exactly:

  ```markdown
  # Wiki Forge
  See `wiki/AGENTS.md` for operational rules and page templates.
  See `wiki/CATEGORIES.md` for the authoritative category taxonomy.
  See `wiki/README.md` for human orientation and architecture.
  See `wiki/WORKFLOWS.md` for step-by-step manual operations.
  Helper scripts ship with the wiki-forge plugin — run them via the `/wiki-*` skills.
  ```

### 4. Helper scripts — provided by the plugin (nothing to install)

Under the wiki-forge plugin model, the ~45 helper scripts are **not** copied
into the project. They ship with the plugin and the `/wiki-*` skills invoke them
as `python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py" <wiki-root> …`. Every
script takes the wiki root as an argument, so one plugin copy serves every wiki
on the machine — there is no `wiki/.scripts/` directory and no copy step.

This is the A1 "pure consumption" model from the Phase-1 collapse: one source of
truth for the machinery, zero mirror tax. The schema/content files (Step 3) and
navigation files (Step 6) are still written per-project because they are
per-wiki content — only the *machinery* (scripts, agents) lives in the plugin.

**Reserve Read+Write for files that get customized at install** — currently just `CATEGORIES.md` (seed-category find-and-replace, per Step 3). The other schema files — `CLAUDE.md`, `AGENTS.md`, `README.md`, `WORKFLOWS.md` — are verbatim and go through `cp` (Step 3).

### 5. Optional: Semantic search setup

The Python **dependencies** (onnxruntime, tokenizers, numpy) are provisioned
automatically by the plugin's SessionStart hook (`hooks/hooks.json` →
`scripts/install_deps.py`), isolated into `${CLAUDE_PLUGIN_DATA}/pylib`. This
step's job is therefore the **per-wiki ONNX model** (~30MB, downloaded into
`<wiki-root>/.meta/models/`) — a wiki-root-relative artifact the deps hook does
not handle.

First, check whether semantic search is fully configured (deps present AND model
downloaded). The `PYTHONPATH` prefix lets the check see the hook-provisioned
deps in `pylib`:

```bash
PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_search.py" --check
```

- If output is `installed`: skip this step entirely. Note "semantic search:
  already configured" in the final report.
- If output is `not installed`: tell the user:

  > "Semantic search enables hybrid keyword+meaning search for larger wikis
  > (~90+ pages). The Python deps are already installed with the plugin; this
  > downloads a ~30MB model. Small wikis work fine without it — the LLM reads
  > indices directly."

  Ask: "Download the semantic-search model now?"

  If yes: run
  `PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_search.py" --yes --wiki <wiki-root>`
  (`--yes` also re-installs the deps into the active env as a fallback, should
  the SessionStart hook not have run yet — e.g. first session with no network.)

  If the model download or a fallback install fails (network issues, pip
  errors), use WebSearch to look up current troubleshooting for the specific
  failure and present the results to the user.

  If no: skip. Note in the report that they can add it later by running
  `PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_search.py" --wiki <wiki-root>`.

Do NOT ask about `zvec` during init — it's only relevant for very large
wikis (2000+ pages). Mention it briefly in the final report as a future
option.

### 6. Seed the navigation files

**Find the navigation templates:** Run Glob for
`**/wiki-init/templates/index*.md`, `**/wiki-init/templates/log.md`,
and `**/wiki-init/templates/overview.md`. Read each file.

Write them to the wiki with these mappings:

- `index.md` → `wiki/pages/indexes/index.md` (master index TOC)
- `index-sources.md` → `wiki/pages/indexes/index-sources.md`
- `index-concepts.md` → `wiki/pages/indexes/index-concepts.md`
- `index-entities.md` → `wiki/pages/indexes/index-entities.md`
- `index-comparisons.md` → `wiki/pages/indexes/index-comparisons.md`
- `index-syntheses.md` → `wiki/pages/indexes/index-syntheses.md`
- `log.md` → `wiki/log.md`
- `overview.md` → `wiki/overview.md`

Replace any `<DATE>` placeholder with today's ISO date (YYYY-MM-DD), and any `<TIME>` placeholder with the current wall-clock time (HH:MM, 24-hour). The init entry in `log.md` must match the `YYYY-MM-DD HH:MM` format every subsequent `append_log.py` call uses — otherwise `/wiki-log --since <date>` filtering and any awk-based log parsing mis-sort the leading entry.

### 7. Optional: Dual-Claude bootstrap (only when `--dual-claude` flag is set)

If `--dual-claude` was NOT passed: skip this entire step.

If `--dual-claude` WAS passed: install the wiki-side identity files and the SessionStart hook so the project supports the dual-Claude shape — a primary Claude Code instance (the wiki, this side) plus a separate restricted instance (the clipper) that drops raw material into `vault/` and communicates via `vault/_mailbox/`.

The clipper-side implementation is **not** scaffolded by this step — only the wiki-side hooks for it are. Authoring the clipper (REST API plugin, browser automation, file-drop service, or any other restricted-write mechanism) is left to the user's project. The skeletons installed here document the contract clipper-side code must honor.

**Templates installed by this step** (sourced from `templates/dual-claude/`):

- `vault/.wiki-identity.md` ← `templates/dual-claude/wiki-identity.md` (rules + protocol; loaded everywhere via `@-include` from project-root `CLAUDE.md`)
- `vault/.wiki-identity-bootstrap.md` ← `templates/dual-claude/wiki-identity-bootstrap.md` (welcome report + wake-up drill; main-session only via SessionStart hook)
- `vault/_mailbox/` directory with a starter `README.md` (canonical mailbox protocol — author or copy from a reference implementation; the skeleton points users to do this manually for now)

**Settings hook merge.** The fragment in `templates/dual-claude/settings-hook.json` carries the `hooks.SessionStart` entry that loads the bootstrap file as `additionalContext` into the main session. Merge into `.claude/settings.json` rather than overwriting:

1. Read existing `.claude/settings.json` if present (create empty `{}` if not).
2. Read `templates/dual-claude/settings-hook.json` and strip the `_comment` field.
3. Deep-merge the `hooks.SessionStart` array — append the new entry rather than replacing any existing `SessionStart` hooks the user may have configured.
4. Write back `.claude/settings.json`.
5. Surface the merge to the user in the final report so they can verify it landed correctly.

**Project-root `CLAUDE.md` `@-include`.** The dual-Claude pattern requires the rules-only identity file to be auto-loaded everywhere, including forked subagents. If the project doesn't already have a project-root `CLAUDE.md`, create one with a single line: `@vault/.wiki-identity.md`. If the project already has a `CLAUDE.md`, append the `@-include` line at the bottom (don't overwrite the user's existing content).

**Recommended additional permissions** for `.claude/settings.local.json` when running with `--dual-claude` (mailbox script):

```json
{
  "permissions": {
    "allow": [
      "Bash(python:*)"
    ]
  }
}
```

**Caveat to surface in the final report.** The skeletons reference a `vault/.clipper-identity.md` and `vault/.clipper-rules.md` for the clipper side; those are NOT installed by this step. The wiki side will work without them, but the dual-Claude pattern is only fully realized once the user authors a clipper implementation and its identity/rules files.

### 8. Git setup

Ask: "Initialize git tracking for the wiki? (recommended — the subagents
commit after every operation)"

- **Yes, parent is a git repo** → stage `wiki/` in the existing repo
- **Yes, parent is not a git repo** → `git init` inside `wiki/`
- **No** → skip (but note: no audit trail for operations)

### 9. Report and next steps

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
- Mention: "Edit `wiki/CATEGORIES.md` to adjust the seed categories for your domain — `lint_wiki.py` reads it at runtime, so changes take effect immediately"
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
        "Bash(python:*)"
      ]
    }
  }
  ```

  Only suggest this — do not write the file without user approval.

- **If `--dual-claude` was passed,** also report:
  - The three files installed under `vault/` (`.wiki-identity.md`, `.wiki-identity-bootstrap.md`, `_mailbox/`)
  - Whether the project-root `CLAUDE.md` was created or appended to (with the `@vault/.wiki-identity.md` line)
  - Whether `.claude/settings.json` was created or merged (with the `SessionStart` hook)
  - The reminder that the **clipper side** (identity, rules, write mechanism) is NOT installed by `/wiki-init` and must be authored by the user before the dual-Claude pattern is fully realized

## Rules

- Never overwrite an existing `wiki/` without explicit user confirmation
- Always install the schema files — `CLAUDE.md` + `AGENTS.md` + `README.md` + `CATEGORIES.md` collectively ARE the schema; none optional
- Helper scripts and subagents ship with the wiki-forge plugin — NOT installed per-project; the `/wiki-*` skills invoke scripts via `${CLAUDE_PLUGIN_ROOT}/scripts/`
- All navigation files (master index, sub-indices, log, overview) must exist
- The seed categories in `CATEGORIES.md` should match the user's domain — don't force AI defaults
