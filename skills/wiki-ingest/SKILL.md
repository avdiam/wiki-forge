---
name: wiki-ingest
description: Compile a vault source file into wiki pages — creating a source summary, editing 5-15 existing pages, creating new pages where warranted, updating index and log, committing. Dispatches to the wiki-ingestor subagent which runs Opus in a forked context to keep heavy file reads out of the main session. Use when the user runs /wiki-ingest, says "ingest this file", "process this source", or after wiki-capture chains into it.
---

# wiki-ingest (dispatcher)

Thin skill that dispatches to the `wiki-ingestor` subagent. All the real
work happens in the subagent.

## Workflow

### 1. Validate input

Expect one of:

- A path to a raw file: `/wiki-ingest vault/2026-04-15-foo.md`
- `all new` — scan `vault/` for unprocessed files
- `rerun <path>` — re-ingest a previously processed file

If no argument given: ask the user which file, or offer to scan `vault/`
for unprocessed files (check against `log.md`).

> [!NOTE]
> If `all new` finds more than 10 unprocessed files AND the wiki has fewer
> than 20 existing pages, warn the user: "Your wiki is still young. Consider
> ingesting 5–10 sources first to establish the taxonomy, then batch the
> rest. Large initial batches produce shallow cross-references because
> there are no existing pages to connect to."

### 2. Locate the wiki

- Find `wiki/CLAUDE.md` in the current directory or upward
- If none found: tell the user, suggest `/wiki-init`, stop

### 3. Dispatch to the wiki-ingestor subagent

> [!IMPORTANT]
> If the user requested to ingest `all new` or provided multiple files,
> you MUST loop through each file and dispatch a separate, independent
> `wiki-ingestor` agent per file. Do not pass multiple files to a single
> ingestor as it will bloat the context window and cause cross-contamination.
> **Dispatch sequentially** (wait for one to complete before starting the
> next) to avoid race conditions on git commits, index files, and the log.

For each file, first run source pre-processing in the main session:

```bash
python <wiki>/.scripts/ingest_source.py --wiki <wiki> --source <raw-file> --json
```

This returns a JSON brief with title, word count, hash, and whether a
summary page already exists (merge mode). Pass this to the ingestor.

Then use the Agent tool to spawn a subagent:
- **model:** `"opus"`
- **description:** `"Wiki ingest — <source title>"`
- **prompt:**

```text
You are the wiki-ingestor subagent. Find your full playbook by running
Glob for '**/agents/wiki-ingestor.md' and read it in full before starting.

Context for this run:
- Source: <raw file path>
- Wiki root: <absolute path to wiki/>
- User hints: <any emphasis/skip guidance the user provided, or "none">

Source brief (from ingest_source.py):
<paste the JSON brief here>

Follow the complete playbook step by step. Respect the >5 contradictions
hard stop. Return a structured report on completion.
```

### 4. Relay the result

When the subagent returns, summarize its report to the user in the main chat:

- Pages created (count and paths)
- Pages updated (count, paths, short notes)
- Contradictions flagged
- New categories proposed (if any)
- Overview refreshed (yes/no)
- Commit hash

## Rules

- Always dispatch — do not do the ingest work in the main session
- One agent per file — never batch multiple files into one ingestor
- If the subagent is unavailable, fall back to running the
  `agents/wiki-ingestor.md` playbook inline
- If the user asks follow-up questions about the ingest, you can answer
  from the subagent's report without re-dispatching
