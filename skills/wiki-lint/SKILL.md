---
name: wiki-lint
description: Health-check the wiki for contradictions, staleness, orphans, broken wikilinks, low confidence, and index drift. Dispatches to the wiki-linter subagent (Opus, forked context) which runs a three-pass workflow — mechanical checks via lint_wiki.py, semantic checks via LLM reasoning, then a report with a fix offer. Use when the user runs /wiki-lint, says "check the wiki", "audit", "clean up the wiki", or after a batch of ingests.
---

# wiki-lint (dispatcher)

*Architecturally: Option B (inline orchestration with Agent-tool dispatch at step 2) per `_template-complex/SKILL.md`.*

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

### 4. Chain to /wiki-overview --soft (when due)

After the linter returns (and after any user-approved fix application),
check whether the overview is due for a soft refresh. The soft-refresh
cadence is every 10 ingests, aligned with the lint cadence.

```bash
# Count ingests since the last mechanical-refresh marker in overview.md
prev_n=$(grep -oP '_Mechanical refresh at ingest #\K\d+' wiki/overview.md | head -1)
curr_n=$(awk '/^## \[.*\] ingest / {c++} END {print c+0}' wiki/log.md)
prev_n=${prev_n:-0}
delta=$((curr_n - prev_n))
```

If `delta >= 10`, invoke `/wiki-overview --soft` (Mode A: mechanical
script + bounded narrative refresh of the `## Recent ingests N–M`
section). The skill owns its own commit and log entry — this skill just
chains and relays.

If `delta < 10`, skip the chain. The next lint run will re-evaluate.

The chain always passes `--soft`; structural realignment (Mode B / full
rewrite) is a separate user decision and never auto-fires.

### 5. Finalize telemetry

The linter has already appended a row to `.meta/lint_log.jsonl` with the
cost fields null as placeholders (`total_tokens`, `input_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`,
`tool_uses`). Parse the `<usage>` block the Agent tool surfaced on
subagent return — extract each field independently with
`<field>:\s*(\d+)` so format drift doesn't lose the row — then rewrite
the last line of `.meta/lint_log.jsonl` with the captured fields
populated. Leave every other field untouched. Missing fields stay null.

If the subagent dispatched more than once in this run (fix-application
turn), aggregate every numeric token field and `tool_uses` across all
turns; use the final turn's wall-clock for `duration_ms` only if the
linter's trailing footer didn't already populate it.

## Rules
- Dispatch via the Agent tool (model: opus); don't run the lint in the main session (it reads many files)
- Reports first, never silently fixes
