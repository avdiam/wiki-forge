---
name: wiki-scale-audit
description: Run the recurring Mode 7 (scale-breakdown) audit — the half-day judgment pass that checks whether the wiki is breaking down at scale across five authoring-side sub-problems (edit-vs-create cost, cascade completeness, concept-overlap drift, graph navigability, top-concept-hub readability). Mechanically gathers the 5 probes via scale_audit_probes.py, walks you through the 2 judgment probes (concept-overlap reading + a graph-view screenshot), drafts a dated audit doc for approval, then resets the re-audit trigger baseline. Use when the user runs /wiki-scale-audit, when the welcome report shows the ⚙ SCALE AUDIT DUE banner, or when the user says "run the scale audit" / "re-audit the wiki at scale". Pass --check to report the trigger verdict only.
argument-hint: "[--check]"
---

# wiki-scale-audit (main-session guided)

*Architecturally: inline main-session procedure, no subagent dispatch. Both prior audits (2026-05-08, 2026-06-06) ran this way — probe 5 needs a user screenshot and the verdict is judgment, neither of which a fork can do alone, and the writeup is rare enough (gated, ~half-day) that fork isolation buys nothing.*

This skill is the **promotion** of the Mode 7 audit from a hand-run procedure
to a codified one. It owns the orchestration of `scale_audit_probes.py`, the two
judgment probes, the approval gate on the verdict, the dated audit doc, the
trigger-baseline reset, the tech-debt two-place update, and the session entry.
The probe script does the mechanical computation; the model (you) does the
judgment and the prose; the user is the decision authority on the verdict.

**Background:** the audit's five sub-problems and their provenance live in
[[llm-wiki-failure-modes-deep-audit]] Mode 7. The two prior dated audit docs
(`dev/2026-05-08-mode7-audit.md`, `dev/2026-06-06-mode7-audit.md`) are the
comparison baselines and the writeup template — read the most recent one before
drafting.

## Trigger model (read this first)

Re-audit triggers are **config-driven**, stored in
`wiki/.meta/scale-audit-config.json` (the live baseline, sibling of
`overview-config.json`). `scale_audit_probes.evaluate()` reads those thresholds
and surfaces a verdict that `drill_state.py` carries into the welcome report as
the `⚙ SCALE AUDIT DUE` banner. **Each audit resets the baseline forward** via
`--init` — that is what stops an already-latched trigger (e.g. `total_pages`,
permanently past its threshold) from nagging the welcome report every session.
This mirrors the `check_overview_triggers.py --init` pattern exactly.

The five triggers (thresholds shown are the current live baseline; check the
config for the authoritative values):

1. **`total_pages >= 800`** — next bin-distribution re-check at the next ~doubling band.
2. **verify-queue composite** — `needs_verification >= 100` sustained across 3 lint runs AND latest `needs_verification + single_source_unverified >= 250`.
3. **`broken_links > 5`** sustained over 5 lint runs (structural cascade-completeness failure).
4. **updated/created ratio inversion** — Q4 `< 1.5` floor OR Q4 `< Q1`.
5. **top concept hub `>= 25_000` words** — Read-limit proxy; concept hubs only (entities have a `supersedes:`/version-sub-page pressure valve). When a verdict calls for decomposing the hub, follow [`references/hub-decomposition.md`](references/hub-decomposition.md) (extract → digest → pointer).

## Workflow

### 1. Locate the wiki + parse the flag

Find `wiki/CLAUDE.md` in the current directory or upward. If none, tell the
user, suggest `/wiki-init`, stop.

- `--check` → run the trigger verdict only (step 2), report, **stop**. No probes,
  no doc, no reset.
- No flag (or `--run`) → full audit (steps 2 → 9).

### 2. Trigger verdict

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/scale_audit_probes.py" wiki --check-triggers
```

Exit 1 = at least one trigger fired; exit 0 = none. The stdout shows the
per-trigger table with current values vs the live-baseline thresholds. For
`--check`, relay this verbatim-ish (which triggers fired, the actual-vs-threshold
numbers, and the `last_audit_date` from the config) and stop.

For a full audit, note which triggers fired — they set the audit's focus and the
`audit_trigger:` frontmatter — and continue. **A full audit is valid even when no
trigger fired** (user-invoked out-of-cycle); say so in the doc if that's the case.

### 3. Gather the mechanical probes

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/scale_audit_probes.py" wiki --json
```

This returns `{probes, triggers, thresholds}`. Probes 1 (inbound distribution),
3 (lint trend), 4 (ingest ratio) are **fully mechanical** — read them straight.
Probes 2 (overlap candidates) and 5 (graph metadata) emit *candidate sets +
instructions* only; their verdicts are judgment work (steps 4–5).

Present a compact summary of the mechanical probes to the user: total pages +
type breakdown, top-3 hubs, the bin distribution vs the prior audit's table, the
lint-trend deltas, and the quartile ratio. Compare against the most recent prior
audit doc's findings — the audit's value is the *delta*, not the snapshot.

### 4. Judgment probe 2 — concept-overlap drift

The script lists 3 high-traffic + 3 low-traffic concept pages with their neighbour
sets. For each candidate, Read the page + 1–3 listed neighbours and check whether
neighbours **agree on shared claims**. Drift = subtle disagreement that doesn't
trip the lint (e.g. two pages quietly asserting different versions of the same
fact). Record per-candidate: clean / cross-linked-and-disambiguated / drifting.
The selective-linking discipline (session 42) is what this probe watches for
erosion of.

### 5. Judgment probe 5 — graph-view UX

This needs a **user screenshot** — main session cannot capture Obsidian's graph
view. Ask:

> Probe 5 needs an Obsidian whole-vault graph-view screenshot (default settings).
> Open the `wiki/` vault, switch to graph view, and paste a screenshot — or say
> "skip" to mark probe 5 not-assessed this firing.

Assess legibility against the script's metadata (components, largest component,
top-hub edge share, orphan count): are hub nodes still distinguishable? Are edges
resolvable to individual lines or merged into a haze? One giant component or
fragmenting? Compare to the prior audit's verdict (the baseline tracks the
"dense-but-legible → decorative" tip).

### 6. Draft the verdict + approval gate

Draft, in a single message to the user:

- **Headline verdict** — is any sub-problem *biting* (not just trending/at-edge)?
- **Per-sub-problem verdict table** — the 5 rows, prior-audit verdict vs this
  firing, with the evidence (model on the prior doc's summary table).
- **What this audit produces** — new tech-debt entries (only for biting
  sub-problems), if any.
- **What this audit does NOT produce** — explicitly, with why (the prior docs
  list elevated-but-managed signals here so the next runner doesn't re-litigate).
- **Forward trigger reset** — the thresholds for the *next* firing. Latched
  triggers move to the next band (the judgment call: e.g. 600→800 pages); unchanged
  triggers stay. State each new threshold value explicitly — step 8 passes them to `--init`.

**Wait for approval.** Do not write the doc, reset the baseline, or touch
tech-debt until the user approves (or supplies edits). The approval gate is
non-skippable — this is a load-bearing, infrequent, judgment-heavy artifact.

### 7. Write the dated audit doc

On approval, write `dev/YYYY-MM-DD-mode7-audit.md` modeled on the most recent
prior audit doc. Frontmatter:

```yaml
---
title: "Mode 7 (Scale-breakdown) audit findings — <Nth> firing"
owner: wiki
not_a_source: true
created: <YYYY-MM-DD>
audit_trigger: "<which triggers fired + the actual values, or 'user-invoked out-of-cycle'>"
supersedes_pointer: <path to the prior audit doc>
---
```

Body sections (per the prior docs): Headline verdict · Snapshot at audit time ·
Probe 1–5 (each with its reading) · Per-sub-problem verdict summary · the biting
sub-problem sizing (if any) · What this audit produces · What this audit does NOT
produce · Trigger reset for the next firing · Audit cost.

### 8. Reset the trigger baseline

Run `--init --force` with the approved forward thresholds, so the welcome banner
reflects the new baseline (and stops firing on the just-latched triggers):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/scale_audit_probes.py" wiki --init --force \
  --total-pages <N> --verify-queue <N> --needs-verification <N> \
  --broken-links <N> --ratio-floor <X> --top-concept-hub-words <N> \
  --audit-date <YYYY-MM-DD> --audit-doc dev/<YYYY-MM-DD>-mode7-audit.md
```

Omit any flag whose threshold is unchanged from the default (it'll keep the
default); pass every flag whose value the audit decided, to be explicit. Confirm
afterward that `python "${CLAUDE_PLUGIN_ROOT}/scripts/run_drill.py" wiki` reports
`scale_audit.should_audit: false` (unless a trigger genuinely still fires under
the new baseline — e.g. a biting sub-problem the user wants to keep surfaced).

### 9. Two-place tech-debt + session entry

- **Update the Mode 7 entry in BOTH `dev/tech-debt.md` AND
  `dev/tech_debt_assessment.md`** (two-place rule, [[non-negotiable]]): refresh
  the "Trigger last-checked" stamp, the forward triggers, and the "first/second
  audit" pointers to the new doc. If the audit surfaced a biting sub-problem,
  **file the new tech-debt entry in both files too**.
- **Prepend a session entry to `dev/sessions.md`** with the cross-session whys:
  which sub-problems shifted vs the prior audit, what the script caught vs what
  needed judgment, and the trigger reset rationale.

### 10. Commit

One commit for the operation:

```bash
git add dev/<date>-mode7-audit.md wiki/.meta/scale-audit-config.json \
        dev/tech-debt.md dev/tech_debt_assessment.md dev/sessions.md
git commit -m "audit: Mode 7 scale-breakdown re-fire (<Nth>) — <one-line verdict>; baseline reset"
```

`wiki/.meta/scale-audit-config.json` is live-only (not mirrored — it's
wiki-instance state, like `overview-config.json`). No script mirror is needed for
an audit *run*; only changes to `scale_audit_probes.py` itself require the
three-tree mirror.

## Rules

- **Never write the doc or reset the baseline before the user approves the
  verdict.** The approval gate (step 6) is non-skippable.
- **Never reset the baseline without writing the audit doc.** The doc is the
  durable record the reset points back to via `--audit-doc`; a silent reset
  destroys the audit trail.
- **Main session, no fork.** Probe 5 needs the user; the verdict is judgment.
- **The audit is the delta, not the snapshot.** Always compare against the most
  recent prior audit doc — a number in isolation isn't a finding.
- **File new tech-debt only for *biting* sub-problems.** Trending / at-edge /
  managed-backlog signals go in "What this audit does NOT produce" with the
  reason, not as new entries — this is how the prior audits avoided entry sprawl.
- **Two-place every tech-debt edit** (`tech-debt.md` + `tech_debt_assessment.md`).

## Why this structure

1. **The script is cheap forward-insurance; the judgment is the audit.** Probes
   1/3/4 are mechanical and the script computes them deterministically; the audit's
   real work is probes 2/5 + the per-sub-problem verdict, which the model and user
   do together. The split keeps the recurring cost bounded.
2. **Config-driven triggers make the recurrence honest.** Without the
   `--init`-reset baseline, the welcome banner would fire forever on the first
   latched trigger (we are permanently past 600 pages). The reset is what makes
   "recurring audit" a real cadence rather than a stuck alarm.
3. **The approval gate protects a rare, load-bearing artifact.** A full audit
   fires a few times a year and can spawn structural decomposition work. The user
   is the decision authority on the verdict; the skill never auto-files findings.
