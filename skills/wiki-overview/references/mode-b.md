# /wiki-overview — Mode B (`--full`) steps 2B–7B

> Moved out of SKILL.md (s137, script audit Wave 3) so soft refreshes don't load it. Read in full when running `--full`; the SKILL's Rules section still applies.

## Mode B — `--full`

### 2B. Pass 1 — dispatch wiki-curator (full-start)

If invoked because of `check_overview_triggers.py`, capture the trigger
reasons string. Otherwise it's `"user-invoked"`.

Capture two values to pass to the curator so the intro-phrase anchor
(see `curator-mode-b.md` § "Mode B — Pass 1 rules") can be
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
Mode B instructions: <this skill's base directory>/references/curator-mode-b.md
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
Mode B instructions: <this skill's base directory>/references/curator-mode-b.md
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

### 6B. Close the operation (overview_log row + telemetry commit)

Wait for both passes' `<usage>` blocks (same rule as 6A). Mode B has two
results from two different agents: pipe **both** verbatim and pass **both**
agent ids, and the script sums every field, `duration_ms` included
(file-checkpoint two-pass rule: one telemetry row per operation).

`--status` is `applied` if Pass 2 wrote and committed, else `cancelled`.
`--commit` is `content_hash` from 5B (Pass 1 doesn't commit). `--ingest-count`
is the count at Pass-1 dispatch (the script reads `log.md` if omitted).

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/close_operation.py" overview --wiki wiki \
  --mode full --status <applied|cancelled> --commit <content_hash> \
  --trigger "<reasons string OR user-invoked>" --ingest-count <N> \
  --agent-id <pass-1-id> --agent-id <pass-2-id> <<'RESULT'
<both passes' results, verbatim, including both <usage>...</usage> blocks>
RESULT
```

The row is committed on its own as `overview: telemetry for full rewrite at
ingest #<N>`, never as an amend of 5B; a `cancelled` row is committed the
same way.

### 7B. Relay

Echo the curator's `result` string to the user. If status was
`cancelled`, say so explicitly so the user knows nothing changed.

---
