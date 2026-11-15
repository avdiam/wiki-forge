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

### 4. Append telemetry — log.md entry + lint_log.jsonl row

The agent does NOT append to `log.md` or `lint_log.jsonl`. It returns the
report with a trailing machine-parsable footer, plus the Agent tool
appends a `<usage>` block. This skill consumes both and invokes the two
appenders directly — same pattern as `/wiki-ingest` step 13 and
`/wiki-overview` step 6A (skill owns telemetry; agent owns the work).

**Parse the agent's footer** (last block of the report):

```
Ingests at run: <N>
Total pages: <N>
Verdict: <clean | issues>
Verdict-summary: <human-readable summary>
Counts: <JSON object>
Fixes committed: <short-hash or "none">
```

**Parse the `<usage>` block(s)** with the shared parser; never regex them
by hand (the harness's syntax has changed before). The block is in the
Agent tool result, or in the completion notification for a background
dispatch. A fix-application turn resumes the same agent and produces a
second block: include every block, and the parser sums all fields,
`duration_ms` included (each block covers only its own turn):

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/parse_agent_usage.py" \
  --agent-id <agent-id> --out .claude/tmp/usage-lint.json <<'USAGE'
<every <usage>...</usage> block for this operation, verbatim>
USAGE
```

**Append the `log.md` entry** — always, whether fixes applied or not.
Clean runs MUST reset the wake-up drill's cadence counter (which scans
`log.md` for `^## \[.*?\] lint ` entries), so this call is non-negotiable.
Treat `Fixes committed: none` as empty-string `--commit` (the script omits
the commit line when blank):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" lint \
  --title "<Verdict-summary from footer>" \
  --commit "<short-hash if Fixes committed != 'none', else ''>" \
  --wiki <wiki>
```

**Append the `lint_log.jsonl` row** — always:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_lint_log.py" \
  --wiki <wiki> \
  --lint-type full \
  --ingest-count-at-run <Ingests at run from footer> \
  --total-pages <Total pages from footer> \
  --counts '<Counts JSON from footer>' \
  --verdict <Verdict from footer> \
  --semantic-pass-ran true \
  --usage-json .claude/tmp/usage-lint.json \
  --commit-hash "<short-hash if Fixes committed != 'none', else ''>"
```

If the footer is malformed or missing required fields, stop and surface
the parsing failure to the user — do not invoke either appender with
placeholder values. A missed row is recoverable; a misclassified one is
not (session 75, 2026-05-22 — a `note`-tagged line broke the lint
counter for the wake-up drill until hand-corrected).

### 5. Commit telemetry — always a separate commit, never an amend

After step 4 the working tree has dirty entries in `wiki/log.md` (the
`lint` entry this skill appended) and `wiki/.meta/lint_log.jsonl` (the
row this skill appended). The agent does NOT commit telemetry itself —
this skill owns it. Never amend the linter's fix-commit to fold these in:
the row records that commit's hash, and an amend would rewrite it,
orphaning the recorded hash (session 132 — same bug as `/wiki-ingest`
step 13 and `/wiki-overview` step 6A had).

Branch on the agent's trailing footer line `Fixes committed: <hash or "none">`:

(a) **Fixes committed** (hash present). HEAD is the linter's `lint: ...`
fix-commit (per agent playbook Pass C). Commit the telemetry on top of it:

```bash
if ! git diff --quiet wiki/log.md wiki/.meta/lint_log.jsonl; then
    git add wiki/log.md wiki/.meta/lint_log.jsonl
    git commit -m "lint: telemetry for <fix-commit hash>"
fi
```

(b) **No fixes** (footer reads `Fixes committed: none`). No linter commit
exists; HEAD is some unrelated prior commit. Create a fresh telemetry
commit so the lint pass has its own audit-trail entry:

```bash
if ! git diff --quiet wiki/log.md wiki/.meta/lint_log.jsonl; then
    git add wiki/log.md wiki/.meta/lint_log.jsonl
    git commit -m "lint: <verdict-summary> at ingest #<N> — <total-pages> pages"
fi
```

Use the agent's report footer to fill `<verdict-summary>` (e.g. `"clean
full pass"` or `"3 flags, no fixes applied"`), `<N>` (Ingests at run),
and `<total-pages>` (Total pages).

The diff-quiet gate is defensive in both branches — covers the case
where the agent's appenders failed and produced no telemetry rows.

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
