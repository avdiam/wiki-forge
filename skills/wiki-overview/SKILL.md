---
name: wiki-overview
description: Refresh wiki/overview.md. Two modes — `--soft` (mechanical bookkeeping refresh + bounded narrative for the latest batch of ingests; chained from /wiki-lint every 10 ingests) and `--full` (full wiki-wide rewrite via wiki-curator with a section-by-section approval gate). Use when the user runs /wiki-overview, when /wiki-lint chains here, when check_overview_triggers.py reports should_rewrite, or when the user says "refresh the overview" / "rewrite overview.md". The skill owns the commit; the curator subagent owns the writes.
argument-hint: "[--soft|--full|--check]"
---

# wiki-overview (orchestrator)

*Architecturally: Option B (inline orchestration with Agent-tool dispatch at step 4A for `--soft` and steps 2B + 4B for `--full`'s file-checkpoint two-pass) per `_template-complex/SKILL.md`.*

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
4. No flag → peek `python wiki/.scripts/check_overview_triggers.py wiki`. If `should_rewrite: true` AND the call is not chained from lint, surface the reasons and ask whether the user wants `--soft` (skip the structural realignment) or `--full` (do it now); default to `--soft` on silence. Otherwise → Mode A.
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
prev_n=$(grep -oP '_Mechanical refresh at ingest #\K\d+' wiki/overview.md | head -1)
prev_n=${prev_n:-0}
```

If the file has no marker (first-ever soft refresh), `prev_n` stays `0`
and step 4A's curator dispatch summarizes from the very first ingest —
acceptable for a fresh wiki.

### 3A. Mechanical refresh

Run the mechanical refresh script. This is fast (~1s), deterministic, and
the only file it touches is `wiki/overview.md` (Stats section + the
mechanical-refresh marker line).

If `/tmp/wiki-graph.json` exists (the wake-up drill stashes it on every
session start, and `/wiki-lint` re-stashes it during Pass A), pass it
through to skip an inline graph rebuild — same pattern as drill step 6's
reuse of step 5's snapshot:

```bash
if [ -f /tmp/wiki-graph.json ]; then
    python wiki/.scripts/update_overview_mechanical.py wiki --graph-json /tmp/wiki-graph.json
else
    python wiki/.scripts/update_overview_mechanical.py wiki
fi
```

The `--graph-json` arm falls back to a fresh inline build if the stash is
unreadable, so this is safe to use opportunistically.

If the script reports "overview.md unchanged", the wiki state is
identical to the last refresh. Skip the curator dispatch and exit.

### 3A.1. Read the new marker

```bash
curr_n=$(grep -oP '_Mechanical refresh at ingest #\K\d+' wiki/overview.md | head -1)
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
python wiki/.scripts/emit_recent_ingests_skeleton.py \
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

Use the Agent tool:

- **subagent_type:** `"wiki-curator"`
- **model:** `"opus"` (frontmatter default)
- **description:** `"Wiki overview soft refresh"`
- **prompt:**

```text
You are the wiki-curator subagent. Find your full playbook by running
Glob for '**/agents/wiki-curator.md' and read it in full before starting.

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

### 5A. Commit

The mechanical script + the curator's bounded edit produce a single diff.
Commit it as one operation:

```bash
cd wiki
git add overview.md
git commit -m "overview: soft refresh at ingest #<curr_n>

- Mechanical: stats / hub list / refresh marker
- Narrative: ingests <prev_n+1>–<curr_n>"
```

If `git status --porcelain wiki/overview.md` is empty (no changes), skip
the commit.

### 6A. Append to log.md

```bash
python wiki/.scripts/append_log.py note \
  --title "overview soft-refresh at ingest #<curr_n>" \
  --note "ingests <prev_n+1>–<curr_n> summarized; mechanical stats refreshed" \
  --wiki wiki
```

### 6A.2. Append to overview_log.jsonl

Parse the curator's `<usage>` block per-field (same tolerant approach as
`/wiki-ingest` step 13 — for each field in `{total_tokens, input_tokens,
cache_creation_input_tokens, cache_read_input_tokens, output_tokens,
tool_uses, duration_ms}`, run `<field>:\s*(\d+)` against the
`<usage>...</usage>` content). Missing fields → null. `status` is
`applied` if step 5A produced a commit, `no_changes` if the commit was
skipped because the diff was empty.

```bash
python wiki/.scripts/append_overview_log.py --wiki wiki \
  --mode soft \
  --ingest-count-at-run <curr_n> \
  --status <applied|no_changes> \
  --total-tokens <N or ""> \
  --input-tokens <N or ""> \
  --cache-creation-input-tokens <N or ""> \
  --cache-read-input-tokens <N or ""> \
  --output-tokens <N or ""> \
  --tool-uses <N or ""> \
  --duration-ms <N> \
  --commit-hash <short-hash or "">
```

If the appender exits non-zero, mention briefly and move on. Telemetry
failure does NOT roll back the operation.

**Fold telemetry into the soft-refresh commit (when step 5A produced one).**
The two appenders above run AFTER step 5A's commit (they need the commit
hash and the curator's `<usage>` block) — left as-is, the `log.md` and
`overview_log.jsonl` rows stay dirty until a later session backfills
them. Amend the soft-refresh commit to fold them in:

```bash
if ! git diff --quiet wiki/log.md wiki/.meta/overview_log.jsonl; then
    git add wiki/log.md wiki/.meta/overview_log.jsonl
    git commit --amend --no-edit
fi
```

The diff-quiet gate is defensive — no-op when an appender failed, when
step 5A skipped its commit because the overview diff was empty, or when
nothing actually changed. HEAD at this point is the soft-refresh commit
from step 5A. Same pattern as `/wiki-ingest` step 13's telemetry fold —
see that SKILL for the wider rationale. Personal-repo amend policy
applies (see [`vault/.wiki-identity.md`](../../../vault/.wiki-identity.md) Obsidian commit policy).

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
that phrase is what `wiki/.scripts/drill_state.py` parses to compute the
welcome report's `Overview:` freshness field after Mode B runs.

Use the Agent tool:

- **subagent_type:** `"wiki-curator"`
- **model:** `"opus"`
- **description:** `"Wiki overview full rewrite — Pass 1"`
- **prompt:**

```text
You are the wiki-curator subagent. Find your full playbook by running
Glob for '**/agents/wiki-curator.md' and read it in full before starting.

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

- **subagent_type:** `"wiki-curator"`
- **model:** `"opus"`
- **description:** `"Wiki overview full rewrite — Pass 2"`
- **prompt:**

```text
You are the wiki-curator subagent. Find your full playbook by running
Glob for '**/agents/wiki-curator.md' and read it in full before starting.

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

### 5B. Commit

If the curator returned `done` with status implying a write occurred,
commit:

```bash
cd wiki
git add overview.md .meta/overview-config.json
git commit -m "overview: full rewrite (Mode B)

<Curator report's first line: trigger summary or 'user-invoked'>
Sections changed: <count>
Pull-quotes: kept K, replaced R, dropped D
Baseline reset."
```

If status is `cancelled`, skip the commit. The checkpoint is already
deleted by the curator.

### 6B. Append to log.md

```bash
python wiki/.scripts/append_log.py note \
  --title "overview full rewrite (Mode B)" \
  --note "<curator's result string>" \
  --wiki wiki
```

### 6B.2. Append to overview_log.jsonl

Mode B emits two `<usage>` blocks (one per pass). Parse each block
per-field (same tolerant approach as Mode A) and **aggregate across
passes** for the row's cost fields per the HITL contract in
`vault/.wiki-identity.md` § Subagent HITL — sum every numeric field
across the two passes; `duration_ms` sums the two passes' wall-clocks.
`commit_hash` is from the Pass-2 commit only (Pass 1 doesn't commit).
`status` is `applied` if Pass 2 wrote and committed, `cancelled` if
Pass 2 returned status `cancelled` (skip the commit entirely).

```bash
python wiki/.scripts/append_overview_log.py --wiki wiki \
  --mode full \
  --trigger "<reasons string OR user-invoked>" \
  --ingest-count-at-run <N> \
  --status <applied|cancelled> \
  --total-tokens <sum or ""> \
  --input-tokens <sum or ""> \
  --cache-creation-input-tokens <sum or ""> \
  --cache-read-input-tokens <sum or ""> \
  --output-tokens <sum or ""> \
  --tool-uses <sum or ""> \
  --duration-ms <sum> \
  --commit-hash <short-hash or "">
```

`<N>` is the ingest count snapshot at Pass-1 dispatch (use
`grep -c "^## \[.*\] ingest" wiki/log.md` if you didn't capture it
upstream).

**Fold telemetry into the full-rewrite commit (when step 5B produced one).**
Same shape as Mode A's fold — the appenders run after step 5B's commit,
so the `log.md` and `overview_log.jsonl` rows stay dirty until amended:

```bash
if ! git diff --quiet wiki/log.md wiki/.meta/overview_log.jsonl; then
    git add wiki/log.md wiki/.meta/overview_log.jsonl
    git commit --amend --no-edit
fi
```

The diff-quiet gate covers the cancelled-status path (no step 5B commit
to amend, no telemetry rows appended → nothing in the diff) and the
appender-failed path. HEAD at this point is the full-rewrite commit
from step 5B when status is `applied`. Personal-repo amend policy
applies (see [`vault/.wiki-identity.md`](../../../vault/.wiki-identity.md) Obsidian commit policy).

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
