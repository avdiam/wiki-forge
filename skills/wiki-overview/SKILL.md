---
name: wiki-overview
description: Refresh wiki/overview.md. Two modes — `--soft` (mechanical bookkeeping refresh + bounded narrative for the latest batch of ingests; chained from /wiki-lint every 10 ingests) and `--full` (full wiki-wide rewrite via wiki-curator with a section-by-section approval gate). Use when the user runs /wiki-overview, when /wiki-lint chains here, when check_overview_triggers.py reports should_rewrite, or when the user says "refresh the overview" / "rewrite overview.md". The skill owns the commit; the curator subagent owns the writes.
argument-hint: "[--soft|--full|--check]"
---

# wiki-overview (orchestrator)

*Architecturally: inline orchestration with Agent-tool dispatch at step 4A for `--soft` and steps 2B + 4B for `--full`'s file-checkpoint two-pass.*

This skill is the **single entry point** to the `wiki-curator` agent. It
owns mode selection, the mechanical refresh script invocation (Mode A
only), the file-checkpoint HITL orchestration (Mode B), and the commit.
The curator subagent never invokes itself and is never invoked by other
skills.

## Mode summary

| Mode | Trigger | Cost | Touches |
|---|---|---|---|
| **`--soft`** (default) | Chained from `/wiki-lint` every 10 ingests, or user-invoked `/wiki-overview` | Mechanical (~1s) + skeleton emit (~1s) + one curator dispatch (synthesis-only, ~30-50% cheaper than pre-session-76) | Stats / hub list / refresh-marker (`update_overview_mechanical.py`); `## Ingests N–M` skeleton (`emit_recent_ingests_skeleton.py`); synthesis tails + section title (curator) |
| **`--full`** | Explicit `/wiki-overview --full`, OR `check_overview_triggers.py` reports `should_rewrite: true`, OR user-invoked when wiki has shifted | Two curator dispatches (Pass 1 + Pass 2) with user approval gate between | Entire `overview.md` (curator) |

## Workflow

### 1. Validate input + locate the wiki

Find `wiki/CLAUDE.md` in the current directory or upward. If none found,
tell the user, suggest `/wiki-init`, stop.

Parse the invocation as a flat cascade:

1. `--check` → run `check_overview_triggers.py` only, report verdict, stop.
2. `--full` → Mode B (skip the trigger check).
3. `--soft` → Mode A.
4. No flag → peek `python "${CLAUDE_PLUGIN_ROOT}/scripts/check_overview_triggers.py" wiki`. If `should_rewrite: true` AND the call is not chained from lint, surface the reasons and ask whether the user wants `--soft` (skip the structural realignment) or `--full` (do it now); default to `--soft` on silence. Otherwise → Mode A.
5. Anything else → reject with usage hint.

Mode B's approval gate is non-skippable, so silent-default-to-soft is not a recovery hazard.

When chained from `/wiki-lint`, the chain always passes `--soft`
explicitly — never run trigger detection in the chain path. The lint
chain is bookkeeping; structural realignment is a separate decision.

---

## Mode A — `--soft`

### 2A. Snapshot the previous ingest marker (BEFORE step 3A overwrites it)

Read the current mechanical-refresh marker from `wiki/overview.md` first.
The script in step 3A will replace this marker with a new one — once it
runs, the previous value is gone unless you've stashed it.

```bash
prev_n=$(grep -o '_Mechanical refresh at ingest #[0-9]*' wiki/overview.md | head -1 | grep -o '[0-9]*$')
prev_n=${prev_n:-0}
```

If the file has no marker (first-ever soft refresh), `prev_n` stays `0`
and step 4A's curator dispatch summarizes from the very first ingest —
acceptable for a fresh wiki.

### 3A. Mechanical refresh

Run the mechanical refresh script. This is fast (~1s), deterministic, and
the only file it touches is `wiki/overview.md` (Stats section + the
mechanical-refresh marker line).

If `.claude/tmp/wiki-graph.json` exists (`/wiki-lint` stashes it during
Pass A, just before chaining here), pass it through to skip an inline graph
rebuild:

```bash
if [ -f .claude/tmp/wiki-graph.json ]; then
    python "${CLAUDE_PLUGIN_ROOT}/scripts/update_overview_mechanical.py" wiki --graph-json .claude/tmp/wiki-graph.json
else
    python "${CLAUDE_PLUGIN_ROOT}/scripts/update_overview_mechanical.py" wiki
fi
```

The `--graph-json` arm falls back to a fresh inline build if the stash is
unreadable, so this is safe to use opportunistically.

If the script reports "overview.md unchanged", the wiki state is
identical to the last refresh. Skip the curator dispatch and exit.

### 3A.1. Read the new marker

```bash
curr_n=$(grep -o '_Mechanical refresh at ingest #[0-9]*' wiki/overview.md | head -1 | grep -o '[0-9]*$')
```

If `curr_n − prev_n < 1`, no new ingests have landed since last refresh —
skip the skeleton emit + curator dispatch and proceed to commit (Stats
may still have shifted from typed-edge growth, etc.).

### 3A.2. Emit the bullet skeleton mechanically

Inject a `## Ingests N–M` section into `overview.md` with the mechanical
components filled in (ingest number, source title, author wikilink,
date, created/updated page lists) and `<SYNTHESIS>` + `<SECTION-TITLE>`
placeholders left for the curator:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/emit_recent_ingests_skeleton.py" \
  --wiki wiki --from $((prev_n + 1)) --to $curr_n
```

The script is idempotent (re-running with the same window replaces the
prior skeleton rather than appending). It inserts just before `## Stats`
when no prior section with the same range exists.

Why this exists: prior to session 76 the curator regenerated the entire
skeleton on every Mode A dispatch — but the skeleton has 6 mechanical
components and 1 judgment component (the synthesis tail per bullet +
the section header). Emitting the skeleton from script saves ~30-50% of
Mode A token cost; the curator's dispatch is now scoped to filling the
two placeholder tokens only.

### 4A. Dispatch wiki-curator with mode: soft

Note the dispatch time — it is the `--duration-ms` fallback in 6A if no
`<usage>` block ever surfaces. Use the Agent tool:

- **subagent_type:** `"wiki-forge:wiki-curator"`
- **description:** `"Wiki overview soft refresh"`
- **prompt:**

```text
Mode: soft
Wiki root: <absolute path to wiki/>
Last refreshed at ingest: <prev_n>
Current ingest count: <curr_n>

The skeleton for `## Ingests <prev_n+1>–<curr_n>` has already been emitted
into overview.md by `emit_recent_ingests_skeleton.py` (skill step 3A.2).
Your scope is narrowed to filling two placeholder tokens:
  - Replace `<SECTION-TITLE>` in the section header with a short thematic
    label distilling the batch.
  - Replace every `<SYNTHESIS>` placeholder (one per bullet) with the
    one-clause framing that connects the ingest to wider wiki threads.

Do NOT regenerate or rewrite the skeleton lines — the mechanical
components (ingest number, title, author wikilink, date, created/updated
lists) are authoritative as written. Editing them is out of scope.

Honor the Mode A scope rule: edit ONLY the `## Ingests <prev_n+1>–<curr_n>`
section of overview.md (and within it, only the two placeholder tokens).
Every other section is off-limits.

Return the structured Mode A report.
```

### 5A. Append to log.md, then commit

The mechanical script + the curator's bounded edit + the `log.md` note
produce a single diff. If `git status --porcelain wiki/overview.md` is
empty (no changes), skip this whole step — no note, no commit — and go
to 6A with `status: no_changes`.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" note \
  --title "overview soft-refresh at ingest #<curr_n>" \
  --note "ingests <prev_n+1>–<curr_n> summarized; mechanical stats refreshed" \
  --wiki wiki
git add wiki/overview.md wiki/log.md
git commit -m "overview: soft refresh at ingest #<curr_n>

- Mechanical: stats / hub list / refresh marker
- Narrative: ingests <prev_n+1>–<curr_n>"
content_hash=$(git rev-parse --short HEAD)
```

Root-relative paths, no `cd` (a bare `cd wiki` leaks into every later
step's relative paths). `content_hash` is final — nothing amends this
commit, so the hash recorded in 6A stays resolvable in history.

### 6A. Append to overview_log.jsonl + telemetry commit

**Wait for the curator's `<usage>` block first.** When the Agent dispatch
in 4A returns synchronously, `<usage>` is in the tool result. When it runs
in the background, the tool result is only a launch ack and `<usage>`
arrives later in the completion `<task-notification>` — do not append
until that notification has landed. Only if no `<usage>` is ever
surfaced: still run the parser (it emits nulls) and pass the main-session
wall-clock since the 4A dispatch as an explicit `--duration-ms` (the
appender requires an integer there).

Parse it with the shared parser (never regex it by hand):

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/parse_agent_usage.py" \
  --agent-id <agent-id> --out .claude/tmp/usage-overview.json <<'USAGE'
<every <usage>...</usage> block for this operation, verbatim>
USAGE
```

`status` is `applied` if step 5A produced a commit, `no_changes` if it
was skipped.
`--commit-hash` is `content_hash` from 5A (`""` for `no_changes`).

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_overview_log.py" --wiki wiki \
  --mode soft \
  --ingest-count-at-run <curr_n> \
  --status <applied|no_changes> \
  --usage-json .claude/tmp/usage-overview.json \
  --commit-hash <short-hash or "">
```

If the appender exits non-zero, mention briefly and move on. Telemetry
failure does NOT roll back the operation.

**Commit the row separately — never amend 5A.** Amending the content
commit to fold the row in rewrites its hash, so the row would point at
an orphaned pre-amend commit (every `overview_log.jsonl` hash through
ingest #321 did — session 132). A separate telemetry commit keeps the
recorded hash valid:

```bash
if ! git diff --quiet wiki/.meta/overview_log.jsonl; then
    git add wiki/.meta/overview_log.jsonl
    git commit -m "overview: telemetry for soft refresh at ingest #<curr_n>"
fi
```

The diff-quiet gate is defensive — no-op when the appender failed. The
`no_changes` path commits its row too, so it never sits dirty into the
next operation.

### 7A. Relay

Tell the user: which ingest range was summarized, that the mechanical
stats refreshed, the commit hash, and (if relevant) any non-zero changes
in graph stats since the previous refresh.

---

## Mode B — `--full`

### 2B. Pass 1 — dispatch wiki-curator (full-start)

If invoked because of `check_overview_triggers.py`, capture the trigger
reasons string. Otherwise it's `"user-invoked"`.

Capture two values to pass to the curator so the intro-phrase anchor
(see `.claude/agents/wiki-curator.md` § "Mode B — Pass 1 rules") can be
written with real numbers:

```bash
ingest_n=$(grep -c "^## \[.*\] ingest " wiki/log.md)
today=$(date +%Y-%m-%d)
```

`ingest_n` is the lifetime ingest count; `today` is the rewrite date.
Both go into the literal phrase `Last full rewrite at ingest #<N>
(<YYYY-MM-DD>)` the curator embeds in the rewritten intro paragraph —
that phrase is what `${CLAUDE_PLUGIN_ROOT}/scripts/drill_state.py` parses to compute the
welcome report's `Overview:` freshness field after Mode B runs.

Use the Agent tool:

- **subagent_type:** `"wiki-forge:wiki-curator"`
- **description:** `"Wiki overview full rewrite — Pass 1"`
- **prompt:**

```text
Mode: full-start
Wiki root: <absolute path to wiki/>
Trigger: <reasons string OR "user-invoked">
Current ingest count: <ingest_n>
Today's date: <today>

Run the Pass 1 contract: read the wider horizon, build the proposed
successor overview, write the section-by-section diff, write the
checkpoint at .claude/tmp/wiki-curator-checkpoint.json, return paused
with the diff as `display`.

Honor the HARD pull-quote preservation rule. Every dropped quote needs
a written justification.

Honor the HARD intro-phrase anchor: the proposed_overview's intro
paragraph must contain the literal phrase `Last full rewrite at ingest
#<Current ingest count> (<Today's date>)`. See your Pass 1 rules.
```

### 3B. Pause point — show diff to user verbatim

The curator returns:

```json
{
  "status": "paused",
  "checkpoint_file": ".claude/tmp/wiki-curator-checkpoint.json",
  "display": "<the section-by-section diff markdown>"
}
```

Print `display` **verbatim** to the user — no framing, no summary, no
introductory line. Then ask:

```text
Approve this rewrite? Reply `approve` to ship it, `reject` to abort,
or describe edits ("keep the Karhade quote", "drop the new section X",
"replace the Cluster map with Y").
```

Wait for the user's reply. Capture it verbatim, including multi-line
content. Do not paraphrase.

### 4B. Pass 2 — dispatch wiki-curator (full-resume)

Use the Agent tool again:

- **subagent_type:** `"wiki-forge:wiki-curator"`
- **description:** `"Wiki overview full rewrite — Pass 2"`
- **prompt:**

```text
Mode: full-resume
Wiki root: <absolute path to wiki/>
Checkpoint file: .claude/tmp/wiki-curator-checkpoint.json
User response:
<the user's reply, verbatim, including newlines>

Apply the user's reply to the proposed_overview in the checkpoint per
your Pass 2 contract. Return done.
```

(The agent's playbook owns the post-write housekeeping — re-init of the
trigger baseline and checkpoint deletion are documented there. The skill
intentionally does not repeat them; otherwise the contract drifts in two
places.)

### 5B. Append to log.md, then commit

If the curator returned `done` with status implying a write occurred,
append the note and commit both together:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" note \
  --title "overview full rewrite (Mode B)" \
  --note "<curator's result string>" \
  --wiki wiki
git add wiki/overview.md wiki/.meta/overview-config.json wiki/log.md
git commit -m "overview: full rewrite (Mode B)

<Curator report's first line: trigger summary or 'user-invoked'>
Sections changed: <count>
Pull-quotes: kept K, replaced R, dropped D
Baseline reset."
content_hash=$(git rev-parse --short HEAD)
```

If status is `cancelled`, skip the note and the commit. The checkpoint
is already deleted by the curator.

### 6B. Append to overview_log.jsonl + telemetry commit

Wait for both passes' `<usage>` blocks (same rule as 6A — a background
dispatch surfaces `<usage>` in its completion notification, not the
launch ack). Mode B emits two `<usage>` blocks (one per pass), from two
different agents. Feed **both** blocks and **both** agent ids to one parser
call; it sums every field, `duration_ms` included (file-checkpoint two-pass
rule: one telemetry row per operation):

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/parse_agent_usage.py" \
  --agent-id <pass-1-id> --agent-id <pass-2-id> --out .claude/tmp/usage-overview-full.json <<'USAGE'
<every <usage>...</usage> block for this operation, verbatim>
USAGE
```

`commit_hash` is `content_hash` from 5B (Pass 1 doesn't commit).
`status` is `applied` if Pass 2 wrote and committed, `cancelled` if
Pass 2 returned status `cancelled` (skip the commit entirely).

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_overview_log.py" --wiki wiki \
  --mode full \
  --trigger "<reasons string OR user-invoked>" \
  --ingest-count-at-run <N> \
  --status <applied|cancelled> \
  --usage-json .claude/tmp/usage-overview-full.json \
  --commit-hash <short-hash or "">
```

`<N>` is the ingest count snapshot at Pass-1 dispatch (use
`grep -c "^## \[.*\] ingest" wiki/log.md` if you didn't capture it
upstream).

**Commit the row separately — never amend 5B** (same reason as 6A: an
amend orphans the hash the row records):

```bash
if ! git diff --quiet wiki/.meta/overview_log.jsonl; then
    git add wiki/.meta/overview_log.jsonl
    git commit -m "overview: telemetry for full rewrite at ingest #<N>"
fi
```

The `cancelled` row is committed the same way; the diff-quiet gate covers
the appender-failed path.

### 7B. Relay

Echo the curator's `result` string to the user. If status was
`cancelled`, say so explicitly so the user knows nothing changed.

---

## Rules

- **Dispatch wiki-curator only via this skill.** No other skill, no main
  session. Mode B's two-pass orchestration relies on this skill being
  the single coordinator.
- **The user's reply in Mode B is verbatim.** Do not reword, summarize,
  or interpret it before passing to Pass 2.
- **Never auto-apply Mode B.** Even when `check_overview_triggers.py`
  reports `should_rewrite: true`, the user must opt in (by passing
  `--full` or replying to the prompt). Trigger detection is a
  recommendation, not a green light.
- **Never run mechanical refresh in Mode B.** Mode B does a full rewrite
  including stats; running the mechanical script first would just be
  overwritten by Pass 2's write.
- **Lint chain only ever runs `--soft`.** The lint→overview chain is
  bookkeeping; structural realignment is a separate user decision.
- **Commit after each successful run** with a descriptive message
  carrying the trigger reason.
- **No silent Mode-B trigger fires.** Every `should_rewrite: true`
  surfaces to the user, never auto-dispatches Pass 1.

## Why this structure

1. **Mode A keeps overview fresh between rewrites** — the daily-bookkeeping
   surface (stats, hubs, recent batch) gets refreshed cheaply on the same
   cadence as `/wiki-lint`. The narrative skeleton (cluster map, themes,
   debates, quotes, unresolved questions) stays stable until structural
   triggers warrant a rewrite.
2. **Mode B is rare and load-bearing** — full rewrites cost a Pass 1 + Pass
   2 dispatch and a user approval gate. The trigger script is the
   detection signal; the user is the decision authority.
3. **The continuity contract is hard** — Pass 1 carries written
   justifications for every drop. Mode B exists specifically to prevent
   silent information loss like the Karhade pull-quote incident.
4. **Single-responsibility curator** — the wiki-curator owns all narrative
   work on overview.md. The ingestor never touches overview anymore.
   Mechanical bookkeeping lives in the script. Three roles, no overlap.
