# Wiki Bootstrap — Main-Session Init

> **Skeleton template installed by `/wiki-init --dual-claude`.** The welcome report spec, drill steps, and dev-mode framing below are mechanical — keep them. Project narrative does not belong here; it belongs in `dev/sessions.md` or git log.

> **Loaded into the main Claude Code session via the `SessionStart` hook in `.claude/settings.json`.** Forked subagents do NOT see this file — by design. The welcome report, wake-up drill, and dev-mode framing are main-session concerns; subagents have no use for them. Non-negotiable rules + HITL pattern + ingest cadence live in [`vault/.wiki-identity.md`](.wiki-identity.md), still `@-included` from project-root `CLAUDE.md` → loaded everywhere including forks.

---

## Welcome report (emit at session start, AFTER the drill, BEFORE anything else)

The welcome report is the session's handshake — emitted as the **first user-visible output**, **mandatory** on every fresh session no matter how trivial the user's opener. Two variants depending on session mode (see § Development mode).

### Template

Both modes share a core block. Banner suffix is `OP MODE` for operational, `DEV MODE` for development.

```
== Wiki bootstrapped — <OP|DEV> MODE ==
- Mailbox: <N open / all clear>
- Git: <clean / N modified, N untracked>
- Clipper rules: <fresh YYYY-MM-DD / STALE — last updated YYYY-MM-DD>
- Memory: <N entries loaded / empty (fresh machine)>
- Lint: <N/10 since last — fresh | OVERDUE by M>
- Graph: hubs <slug-1> (N), <slug-2> (N), <slug-3> (N) | <clean (0 orphans, 1 component) | K orphans, M components> | typed: <C>c/<S>s/<U>u | none
- Overview: <fresh | N ingests stale since soft-update><; structural triggers: <reasons> if check_overview_triggers reports should_rewrite>
- Backlog: <all clear | N pending, untriaged | N pending, triaged — Batch X next (<label>) | N pending, triage STALE — K new since YYYY-MM-DD>
```

**Operational mode** closes immediately with:

```
Ready. What's the task?

<optional offer lines, one per applicable condition>
```

**Development mode** appends three fields after Backlog, then closes with a different prompt:

```
- System loaded: <N agents, N skills, N scripts, schema, workflows>
- Tech-debt queue: <N Immediate, N Conditional, N Design pending, N Future, N Housekeeping>
  - Actionable now: <2–4 items, one per line as `<title> (<tier>)` — pull from Immediate (always actionable) plus any Conditional / Design entries whose triggers are met or near-met. Skip items gated on unmet empirical conditions.>
  - Suggested pickup: <one item, or two that sequence well — one-sentence rationale grounded in the entry's body. Bias toward Immediate over Conditional, and toward items whose touch-list is already in scope.>
- Last session: <YYYY-MM-DD — short title from dev/sessions.md>

System loaded. What are we working on?

<optional offer lines>
```

The `Actionable now` + `Suggested pickup` lines are computed from the tech-debt file you just read in dev-bootstrap step 3 — they replace "should I look through tech-debt myself?" friction with a concrete starting point. The user is free to redirect.

### Rules common to both variants

- Mandatory on every fresh session. Carries **live** values from the drill, not placeholders. If you can't fill real values, you haven't done the drill — don't emit.
- **Emit as plain markdown text, NOT inside a triple-backtick code fence.** The fences in the templates above are documentation formatting only. Some clients collapse messages whose visible body is only a fenced block, making the report invisible to the user. Keep the literal `==` banner, dash-prefixed fields, "Ready." / "System loaded." line, and offer lines — just no surrounding fence.
- **Banners above the report** (any that apply, stacked):
  - `[URGENT]` mailbox message → surface subject + sender above everything else.
  - `⚠ LINT OVERDUE — N ingests since last full lint (cadence: every 10)` when N ≥ 10. Stays until `/wiki-lint` runs clean and appends a new `lint` entry to `log.md`.
- **Offer lines below the report** (max 2, blank line separator). Order: backlog offer first, lint offer second.
  - `N pending, untriaged` → `Offer: run /wiki-triage to organize these into batches?`
  - `triage STALE` → `Offer: refresh triage with /wiki-triage to capture the K new files?`
  - `triaged — Batch X next` → `Offer: pick up Batch X (<label>) with /wiki-ingest? First up: <first article slug>.`
  - `all clear` → no backlog offer.
  - Lint OVERDUE → `Offer: run /wiki-lint now to reset the counter?`
  - Overview stale ≥10 → `Offer: run /wiki-overview --soft to refresh stats + recent-batch narrative?`
  - Overview structural triggers fired → `Offer: run /wiki-overview --full — structural triggers fired (<reasons>); needs Mode B rewrite with approval gate.`
- **Dev mode suppresses backlog offers.** Pushing the ingest queue forward is operational work — dev mode is for system polish. The `Backlog:` field still renders informationally so dev sessions know the operational state, but the `pick up Batch X` / `run /wiki-triage` offer line never fires. Lint and overview offers still fire in both modes.
- Offers are proposals; user must approve before dispatch.
- **Ambiguous opener** (`hi`, `?`, `yo`): default to operational, append `_(For system dev work, say "dev mode" or name a tech-debt item.)_`.

---

## Wake-up drill (mandatory, every session start, BEFORE any user-facing output)

Order matters. Run all of these regardless of the user's opener:

1. **Sweep `vault/_mailbox/`.** List `vault/_mailbox/*.md` (top-level only — skip `.archive/` and `README.md`), read any `to: wiki` + `status: open`, reply / acknowledge / close, flip status. Poll-on-wake is non-negotiable. Any `[URGENT]` subject surfaces ABOVE the welcome report.

   **After the sweep, archive aged terminal-status messages.** Mtime-based, age + status. `closed` / `acknowledged` archive at 7 days; `replied` archives at 14 days; `open` never archives. Move into month-bucketed dirs under `vault/_mailbox/.archive/YYYY-MM/`.
   ```bash
   python wiki/.scripts/archive_mailbox.py vault/_mailbox
   ```
2. **`git status` + commit any clipper-side vault arrivals.** If untracked files exist under `vault/` content folders (but never under `_mailbox/` / `.skipped/` / dot-prefixed dirs), commit them in a single sweep before doing anything else. Customize the path glob below to match your clipper's drop directory:
   ```bash
   if git status --porcelain "vault/Web Articles/" | grep -q '^??'; then
       git add "vault/Web Articles/" && git commit -m "vault: clipper backlog as of $(date +%Y-%m-%d)"
   fi
   ```
   Skip the commit if working-tree dirty for unrelated reasons — sweep only the vault content directories.
3. **`vault/.clipper-rules.md` staleness check** — I own it. Refresh "Coverage hints" if wiki has grown ≥10 ingests since `last_updated`.
4. **Memory probe** — glance at `~/.claude/projects/<...>/memory/MEMORY.md` if present. Absence = fresh machine.
5. **Graph topology snapshot** — top 3 hubs (by inbound wikilinks), orphan count, connected-component count, and typed-edge counts (`contradicts:` / `supersedes:` / `superseded_by:` from frontmatter) from `graph_analyzer.py`. Daily quality signal — surfaces saturation, structural drift, and typed-edge accumulation. **Stash the JSON to `/tmp/wiki-graph.json`** so step 6's drill-state aggregator can reuse it:
   ```bash
   python wiki/.scripts/graph_analyzer.py wiki --json > /tmp/wiki-graph.json
   python wiki/.scripts/format_graph_snapshot.py /tmp/wiki-graph.json
   ```
6. **Drill state aggregator** — `wiki/.scripts/drill_state.py` consolidates the lint cadence, the overview staleness + structural-trigger check, and the ingest-backlog state machine into one JSON object for the welcome-report renderer:
   ```bash
   python wiki/.scripts/drill_state.py wiki --graph-json /tmp/wiki-graph.json
   ```
   The returned JSON maps directly onto welcome-report fields:
   - `lint_counter` → `Lint: N/10 since last — fresh`. When `lint_overdue: true` (counter ≥10), surface the `⚠ LINT OVERDUE` banner ABOVE the report.
   - `overview.stale_by` + `overview.fresh` → `Overview: fresh` (when `stale_by < 10`) or `Overview: N ingests stale since soft-update` (when ≥10).
   - `overview.should_rewrite` + `overview.trigger_reasons` → when true, append `; structural triggers: <reasons>` to the Overview field and emit the Mode B offer line.
   - `backlog.pending` + `backlog.state` → `Backlog: <state-string>`.
   - `backlog.batch_first_slug` → fed into the `Batch X next` offer line as `First up: <slug>`.
7. **Emit the welcome report** (see top of file).
8. **Stamp session mode** for the statusline:
   ```bash
   printf 'op' > .claude/.session-mode    # operational
   printf 'dev' > .claude/.session-mode   # dev
   ```
   No trailing newline. File is git-ignored.

A greeting is **not** a reason to skip the drill.

---

## Development mode

After the drill, choose mode based on the user's opening signal.

**Operational (default).** Normal wiki co-op: mailbox, `/wiki-capture`, `/wiki-ingest`, `/wiki-lint`, `/wiki-query`, `/wiki-log`. Schema, skills, agents auto-load on demand. Default unless dev-oriented signal is clear.

**Dev mode.** User is working *on the system itself*. Triggers (any one):
- Keywords: `"continue development"`, `"work on the system"`, `"tech-debt"`, `"fix bug"`, `"refactor"`, `"add skill"`, `"change agent"`, `"audit"`, `"evaluate the system"`.
- Explicit: `"dev mode"` / `"development mode"` / `/dev`.
- Names a tech-debt item.
- Asks me to read system files.

If genuinely ambiguous, default to operational and offer dev mode in the welcome report.

### Dev-mode bootstrap (extends the drill)

**Lean by design.** Preload only what's load-bearing for picking up tech-debt work; everything else is on-demand. Reading every agent / skill / script / protocol file just-in-case bloats the context with reference material that the actual work either doesn't touch or would re-read in scope anyway.

1. Run the normal wake-up drill steps 1–6 (mailbox, git, clipper-rules, memory, graph, drill state).
2. **Project tree** — cheap structural map of what exists, so on-demand reads know where to look. Customize the prune list to skip your wiki's content directories so dev sessions don't drown in markdown filenames:
   ```bash
   find . -type d \( -name node_modules -o -name .git -o -name __pycache__ -o -name .venv -o -name "Web Articles" -o -name ".obsidian" -o -name ".archive" -o -path "./wiki/pages" \) -prune -o -print 2>/dev/null
   ```
3. **Read these — and only these:**
   - [`dev/tech-debt.md`](../dev/tech-debt.md) — the forward-work queue. **Don't read it whole.** Two-step pattern: (a) Grep the file for `^## ` and `^- \*\*` headers to enumerate sections + entry titles + triggers; (b) targeted Read of the Immediate section in full plus only those Conditional / Design entries whose triggers are met or near-met. Skip entry bodies for items gated on unmet empirical conditions.
   - The **most recent 2–3 entries** of [`dev/sessions.md`](../dev/sessions.md) for "why we're here." Use `Read` with `limit: 60`. Older entries are on-demand.
4. **Everything else is on-demand.** Don't preload — fetch when the work actually touches it: agent playbooks, skills, helper scripts, wiki schema, cross-instance protocol files, hooks reference, settings.
5. Emit the dev-mode welcome report.
6. Stamp `printf 'dev' > .claude/.session-mode`.

Project root [`CLAUDE.md`](../CLAUDE.md), [`vault/.wiki-identity.md`](.wiki-identity.md), and this bootstrap file auto-load via the `@-include` mechanism + the `SessionStart` hook — no manual read needed. Same for `wiki/CLAUDE.md` once a tool call lands inside `wiki/`.

**Mid-session switch.** `"switch to dev mode"` → run unexecuted dev-bootstrap steps, emit dev report, re-stamp `dev`.

---

The user can force a re-bootstrap by re-reading this file and re-running the wake-up drill above, e.g., *"Bootstrap wiki. Sweep mailbox. Report."*
