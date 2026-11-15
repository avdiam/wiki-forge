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

`Scripts dir` is the absolute path of the plugin's `scripts/` directory,
the sibling of this skill's base directory two levels up
(`<base>/../../scripts`). Agents can't rely on `${CLAUDE_PLUGIN_ROOT}`
expanding in their shell, so pass it resolved. Use the Agent tool:

- **subagent_type:** `"wiki-forge:wiki-curator"`
- **description:** `"Wiki overview soft refresh"`
- **prompt:**

```text
Mode: soft
Wiki root: <absolute path to wiki/>
Scripts dir: <absolute path to the plugin's scripts/ directory>
Last refreshed at ingest: <prev_n>
Current ingest count: <curr_n>

The skeleton for `## Ingests <prev_n+1>–<curr_n>` is already in overview.md
(skill step 3A.2). Do not open overview.md: print the brief with
`emit_recent_ingests_skeleton.py --brief`, write the fill JSON, and apply it
with `--fill`, per your Mode A steps. Only the `<SECTION-TITLE>` and
`<SYNTHESIS>` tokens may change; the script enforces this.

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

### 6A. Close the operation (overview_log row + telemetry commit)

**Wait for the curator's `<usage>` block first.** It is in the Agent tool
result for a synchronous dispatch, or in the completion `<task-notification>`
for a background one. Do not close until that notification has landed. If no
block ever arrives, close anyway: duration falls back to the transcript.

`--status` is `applied` if step 5A produced a commit, else `no_changes`.
`--commit` is `content_hash` from 5A (omit it for `no_changes`). The ingest
count is read from `log.md` unless you pass `--ingest-count <curr_n>`.

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/close_operation.py" overview --wiki wiki \
  --mode soft --status <applied|no_changes> --commit <content_hash> \
  --ingest-count <curr_n> --agent-id <agent-id> <<'RESULT'
<the curator's result, verbatim, including <usage>...</usage>>
RESULT
```

The script appends the row and commits it on its own as `overview: telemetry
for soft refresh at ingest #<curr_n>`. It never amends 5A: an amend rewrites
the hash the row records (s132). The `no_changes` row is committed too, so it
never sits dirty into the next operation. Report any `warnings` in one line;
telemetry failure never rolls back the operation.

### 7A. Relay

Tell the user: which ingest range was summarized, that the mechanical
stats refreshed, the commit hash, and (if relevant) any non-zero changes
in graph stats since the previous refresh.

---

## Mode B — `--full`

Read [references/mode-b.md](references/mode-b.md) in full and follow steps
2B–7B there: Pass 1 dispatch, the verbatim pause point, Pass 2, commit,
close, relay. Both curator dispatches must carry `Mode B instructions:
<this skill's base directory>/references/curator-mode-b.md`, because the
curator's Mode B contract lives in that file.

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
