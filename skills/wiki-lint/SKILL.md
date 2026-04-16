---
name: wiki-lint
description: Health-check the wiki for contradictions, staleness, orphans, broken wikilinks, low confidence, and index drift. Dispatches to the wiki-linter subagent (Opus, forked context) which runs a three-pass workflow — mechanical checks via lint_wiki.py, semantic checks via LLM reasoning, then a report with a fix offer. Use when the user runs /wiki-lint, says "check the wiki", "audit", "clean up the wiki", or after a batch of ingests.
---

# wiki-lint (dispatcher)

Thin skill that dispatches to the `wiki-linter` subagent.

## Workflow

### 1. Locate the wiki
- Find `wiki/CLAUDE.md`
- If none: tell the user, suggest `/wiki-init`

### 2. Dispatch to wiki-linter

Use the Agent tool to spawn a subagent:
- **model:** `"opus"`
- **description:** `"Wiki lint health check"`
- **prompt:**

```text
You are the wiki-linter subagent. Find your full playbook by running
Glob for '**/agents/wiki-linter.md' and read it in full before starting.

Context for this run:
- Wiki root: <absolute path to wiki/>

Run the three-pass workflow:
  Pass A: Mechanical (wiki/.scripts/lint_wiki.py --json)
  Pass B: Semantic (read flagged pages, detect real contradictions and stale facts)
  Pass C: Human-readable report with prioritized fix offer

Follow the complete playbook. Do not auto-apply fixes without explicit
user approval.
```

### 3. Relay
Return the report to the user. If the linter asked about applying fixes,
relay the offer and the user's answer back to the subagent.

## Rules
- Dispatch via the Agent tool (model: opus); don't run the lint in the main session (it reads many files)
- Reports first, never silently fixes
