---
name: wiki-lint
description: Health-check the wiki for contradictions, staleness, orphans, broken wikilinks, low confidence, and index drift. Dispatches to the wiki-linter subagent (Opus, forked context) which runs a three-pass workflow — mechanical checks via lint_wiki.py, semantic checks via LLM reasoning, then a report with a fix offer. Use when the user runs /wiki-lint, says "check the wiki", "audit", "clean up the wiki", or after a batch of ingests.
---

# wiki-lint (dispatcher)

*Architecturally: inline orchestration with Agent-tool dispatch at step 2.*

Thin skill that dispatches to the `wiki-linter` subagent.

## Workflow

### 1. Locate the wiki
- Find `wiki/CLAUDE.md`
- If none: tell the user, suggest `/wiki-init`

### 2. Dispatch to wiki-linter

Use the Agent tool to spawn a subagent:
- **subagent_type:** `"wiki-forge:wiki-linter"`
- **description:** `"Wiki lint health check"`
- **prompt:**

```text
Context for this run:
- Wiki root: <absolute path to wiki/>

Run the three-pass workflow:
  Pass A: Mechanical (${CLAUDE_PLUGIN_ROOT}/scripts/lint_wiki.py --summary)
  Pass B: Semantic (read flagged pages, detect real contradictions and stale facts)
  Pass C: Human-readable report with prioritized fix offer

Follow the complete playbook. Do not auto-apply fixes without explicit
user approval.
```

### 3. Relay
Return the report to the user. If the linter asked about applying fixes,
relay the offer and the user's answer back to the subagent (SendMessage to
its agent ID), **quoted verbatim and labeled with its origin**
(`User reply (verbatim, AskUserQuestion answer): "<reply>"`). Subagents
refuse paraphrased approvals as agent-originated (s136).

### 4. Close the operation (telemetry + commit)

The agent does NOT append to `log.md` or `lint_log.jsonl`. It returns the
report with a trailing machine-parsable footer (`Ingests at run`, `Total
pages`, `Verdict`, `Verdict-summary`, `Counts`, `Fixes committed`), and the
Agent tool appends a `<usage>` block. One call consumes both. Pipe **every**
Agent result for this lint into it verbatim: a fix-application turn resumes
the same agent and returns a second result with its own `<usage>` block, and
every block is summed, `duration_ms` included. For a background dispatch, use
the completion notification.

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/close_operation.py" lint --wiki <wiki> \
  --agent-id <agent-id> <<'RESULT'
<every Agent result for this lint, verbatim, including each <usage>...</usage>>
RESULT
```

The script:

- **Parses the footer strictly.** A missing or malformed field exits 2 and writes nothing; surface the parsing failure to the user. A missed row is recoverable; a misclassified one is not (s75: a `note`-tagged line broke the drill's lint counter).
- **Appends the `log.md` lint entry**, always. Clean runs must reset the wake-up drill's cadence counter, which scans `log.md` for `^## \[.*?\] lint `. `Fixes committed: none` becomes an empty commit line.
- **Appends the `lint_log.jsonl` row** (`full`, semantic pass ran) with the cost fields from `<usage>` and the transcript.
- **Commits telemetry separately, never as an amend** (the row records the fix commit's hash; an amend would orphan it, s132): `lint: telemetry for <fix hash>` when fixes were committed, else `lint: <verdict-summary> at ingest #<N> — <pages> pages`.

Output is one JSON line: `row`, `log_entry`, `fix_commit`, `telemetry_commit`,
`verdict`, `ingest_count`, `usage`, `warnings`. Report `warnings` in one line;
never retry.

### 6. Chain to /wiki-overview --soft (when due)

After the lint telemetry has its own commit (step 5), check whether the
overview is due for a soft refresh. The soft-refresh cadence is every 10
ingests, aligned with the lint cadence.

```bash
# Count ingests since the last mechanical-refresh marker in overview.md
prev_n=$(grep -o '_Mechanical refresh at ingest #[0-9]*' wiki/overview.md | head -1 | grep -o '[0-9]*$')
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

## Rules
- Dispatch via the Agent tool (`subagent_type: "wiki-forge:wiki-linter"`; model, effort and turn cap come from the agent's frontmatter); don't run the lint in the main session (it reads many files)
- Reports first, never silently fixes
