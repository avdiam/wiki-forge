---
name: wiki-summarize
description: Produce a structured topic-coverage report from the wiki — five sections (Themes / Agreements / Disagreements / Coverage gaps / Structural footprint) auditing what the wiki currently knows about a topic. Distinct from /wiki-query (specific question, prose synthesis) and /wiki-graph (deterministic graph traversal). Use when the user runs /wiki-summarize, asks "what does the wiki know about X", "summarize coverage of X", or wants the topic audit rather than a question-answer or a graph walk.
argument-hint: "<topic>"
---

# wiki-summarize

User-facing topic-coverage skill. Produces a five-section audit of the wiki's
material on a topic:

- **Themes** — sub-areas the wiki has accumulated material on.
- **Agreements** — cross-source claims that align.
- **Disagreements** — contradictions, open questions, existing `> [!WARNING]` callouts.
- **Coverage gaps** — single-source pages (`verify: true`), low-confidence pages (`confidence < 0.7`), and pages stale relative to newer adjacent ingests.
- **Structural footprint** — page count, hub status (inbound count of the anchor page if it's a hub), connected-component placement.

> Direct invocation. For a natural-language entry point that auto-routes between this, `/wiki-query`, and `/wiki-graph`, see `/wiki-ask`.

**Two-shape retrieval.** Shape A (slug-anchored): when `<topic>` resolves to an
existing page slug, retrieval = the anchor page + its depth-1 wikilink neighbors
(via `${CLAUDE_PLUGIN_ROOT}/scripts/graph_query.py`). Shape B (fuzzy): otherwise, retrieval =
top-25 hybrid-search results (via `${CLAUDE_PLUGIN_ROOT}/scripts/hybrid_search.py`). Auto-detect.

**Always dispatches the librarian** (no inline path). Surveys benefit from
synthesis regardless of wiki size — the five-section shape is non-trivial to
produce inline.

*Architecturally: inline orchestration with Agent-tool dispatch at step 4.*

## Workflow

### 1. Validate input

Expect a single positional argument: a topic. Bare slug (`rag`), `[[wikilink]]`,
or multi-word phrase (`"agent memory architectures"`). If empty, ask what topic
to survey.

### 2. Locate the wiki

- Find `wiki/CLAUDE.md` in the current directory or upward.
- If none: tell the user, suggest `/wiki-init`.

### 3. Two-shape retrieval

Try slug resolution first; fall through to fuzzy search on miss. One script
call does it. The topic goes in on stdin through a single-quoted heredoc, so
`"`, `\`, `$`, backticks and apostrophes need no escaping. Replace `<topic>`
literally; don't pre-escape:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/graph_query.py" <wiki> retrieve --topic-stdin <<'TOPIC_EOF'
<topic>
TOPIC_EOF
```

The topic resolves if it is a slug, a `[[slug]]`, a `slug.md`, or a title
that kebab-cases to a slug ("Agent Memory Systems" → `agent-memory-systems`).

The fourth field on the SHAPE_A line is the anchor's inbound-link count — pass
this to the librarian via the prompt for the **Structural footprint** section
(hub status: ≥50 inbound = hub).

Capture stdout. The first line tells you which shape; subsequent lines are the
**page paths** (Shape A only — relative to wiki root, e.g., `pages/concepts/rag.md`).
Lines starting with `# UNRESOLVED:` indicate a slug that's in the graph but has
no corresponding `.md` file in the canonical type-dirs — surface as a warning
in the relay (rare; signals graph drift) but proceed with the resolved pages.

#### Shape A — slug-anchored

- `retrieval_shape = "slug-anchored"`, `coverage_anchor_slug = <resolved slug>`.
- Page list = anchor + depth-1 neighbors. Sort: anchor first, then neighbors in
  the order returned by `neighbors_within` (sorted by hop-distance asc, slug asc).
- Page paths come from the script (it scans `pages/<type>/` once); the
  librarian receives paths directly, no Glob round-trips needed.
- **Orphan case** (anchor exists but 0 neighbors): proceed with the single page.
  The five-section survey will surface the isolation honestly in **Structural footprint**.

#### Shape B — fuzzy

- `retrieval_shape = "hybrid"`, `coverage_anchor_slug = null`.
- Run hybrid search at higher top-k than `/wiki-query` (surveys want recall, not
  precision). Use the `--queries-json` heredoc form `/wiki-query` Path B
  established — survives quotes/backslashes/special chars in the topic safely:
  ```bash
  PYTHONPATH="${CLAUDE_PLUGIN_DATA}/pylib" python "${CLAUDE_PLUGIN_ROOT}/scripts/hybrid_search.py" <wiki> --queries-json - --top-k 25 <<'JSON'
  ["<topic-json-escaped>"]
  JSON
  ```
  Where `<topic-json-escaped>` is `<topic>` with `\` first escaped to `\\`, then
  `"` escaped to `\"` (JSON string-content rules; tabs/newlines also need
  `\t`/`\n`). The single-quoted `<<'JSON'` ensures no shell expansion.
- Page list = JSON `results[].path` (the script's normalized field).
- **Empty-result guard:** if `results` is empty, do **NOT** dispatch the librarian.
  Emit the dual onramp inline:
  > The wiki has no material on **<topic>**. Two ways forward:
  > 1. **`/wiki-capture <source>`** — if you have a source on this topic, capture it.
  > 2. **Web search** — want me to look for authoritative sources you could capture? (host harness's web-search tool, e.g., `WebSearch`).
  >
  > Both onramps are mandatory — pick either or both.
  Skip steps 4–6. Do not write a telemetry row for an empty dispatch.

### 4. Dispatch wiki-librarian (survey mode)

Use the Agent tool to spawn the wiki-librarian:
- **subagent_type:** `"wiki-forge:wiki-librarian"`
- **description:** `"Wiki survey — <topic>"`
- **prompt:**

```text
Survey mode: true
Survey instructions: <this skill's base directory>/references/librarian-survey.md

Context for this run:
- Topic: <user input>
- Wiki root: <absolute path to wiki/>
- Retrieval shape: <slug-anchored | hybrid>
- Coverage anchor slug: <slug or null>

Pages to read:
- <path 1> — <title or slug>
  <hop-distance hint for Shape A; search snippet for Shape B>
- <path 2> — ...
- ...

Cover these pages at the depth the survey instructions file sets
(Shape B: ranks 1–15 in full, 16–25 skimmed with promotion; Shape A:
all in full) and produce the five-section topic-coverage survey per it
(Themes / Agreements / Disagreements / Coverage gaps / Structural
footprint). Offer to file as syntheses/<slug>-coverage.md (overwrite
on re-run).
```

The librarian's playbook owns the five-section template, the dual-onramp
scoping (route "we don't have material on sub-area X" findings into
**Coverage gaps** rather than interrupting with capture/web-search prompts),
and the filing prose. This skill just dispatches.

### 5. User-gated filing

The librarian's last user-visible turn includes the filing question and, on
opt-in, files `syntheses/<slug>-coverage.md` with frontmatter:

```yaml
---
title: "<topic> — coverage report"
type: synthesis
subtype: coverage-report
category: <category from anchor or dominant page>
summary: "<one-sentence audit headline>"
tags: [coverage-report, <topic-tags>]
aliases: []
confidence: 0.7
sources:
  - vault/<source-1>.md
coverage_anchor_slug: <slug>   # null on Shape B
last_updated: <YYYY-MM-DD>
stale: false
---
```

Re-run convention is **overwrite** — `<slug>-coverage.md` is the canonical
report; old versions live in `git log`.

You are the user gate: relay the filing question to the user; on opt-in,
resume the librarian with a plain **instruction**, not a consent claim
(`File it as pages/syntheses/<slug>-coverage.md.`), carrying any user edits
as instructions. Don't quote the user's reply or say "the user approved":
the librarian takes filing orders from the coordinator by design, and a
message framed as relayed user approval (even a verbatim quote) invites a
refusal (s138).

### 6. Close the operation (query_log row + telemetry commit)

Pipe the librarian's result verbatim. The script parses `Pages consulted:
[[…]]`, `Pages skimmed:` / `Pages promoted:` (Shape B read split; absent →
null) and `Filed as:` (`Not filed` → null) from the footer and the cost
fields from `<usage>` plus the transcript (duration falls back to the
transcript if no block arrived). It commits the row on its own as
`summarize: telemetry` (explicit path, never `git add .`).

```bash
# <agent-id>: the agentId in the Agent tool result (launch ack for background runs)
python "${CLAUDE_PLUGIN_ROOT}/scripts/close_operation.py" summarize --wiki <wiki> \
  --query "<raw topic>" --search-mode <slug-anchored|hybrid> \
  [--top-score <Shape B: top result's normalized score>] \
  --agent-id <agent-id> <<'RESULT'
<the librarian's result, verbatim, including <usage>...</usage>>
RESULT
```

Shape A → `slug-anchored` with no `--top-score`; Shape B → `hybrid`.
`/wiki-summarize` does not expand queries. If this invocation was routed from
`/wiki-ask`, add `--routed-from ask`. Telemetry is observational: report
`warnings` briefly in the relay, but do NOT retry and do NOT fail the survey.

### 7. Relay

Return the librarian's survey to the user. Footer (same parser contract as
`/wiki-query`):

```text
Pages consulted: [[page-1]], [[page-2]], …
Filed as: syntheses/<slug>-coverage.md   (or: Not filed)
```

## Rules

- **Always dispatches the librarian.** No inline path. Survey output is
  non-trivial to produce inline; the cost is justified by the structured
  five-section payoff.
- **Shape auto-detect, no asking.** If the topic resolves to a slug, Shape A
  fires silently. If not, Shape B fires silently. Never ask the user "is this a
  slug or a phrase?" — the resolution is mechanical.
- **Slug ambiguity → ask once.** If the resolved slug clashes with another
  resolvable slug (e.g., `karpathy` matches both `andrej-karpathy` and
  `karpathy-llm-wiki-pattern`), ask a single bounded multiple-choice
  clarification before dispatching. Lifted from `/wiki-graph`'s convention.
- **Empty Shape B = skill-side onramp.** Do not dispatch the librarian on zero
  hits. The dual-onramp output IS the answer.
- **Filing is user-gated.** Per project HITL discipline, never file without
  explicit opt-in.
- **Re-run = overwrite.** `<slug>-coverage.md` is the canonical report; second
  run on the same topic overwrites the body and advances `last_updated`. Do not
  date-prefix.
- **No edits to log.md.** Read operations don't append to `log.md`;
  `query_log.jsonl` is the audit trail. Same as `/wiki-query` and `/wiki-graph`.
- **Top-k for Shape B is hardcoded at 25.** Survey-mode just *is* this — one
  fewer knob the user thinks about. If telemetry later shows recall problems,
  re-tune in a follow-up.
- **Two-tier read for Shape B (s144).** Ranks 1–15 are read in full, ranks
  16–25 skimmed, with promotion to a full read when the skim turns up
  something substantive (rule in `references/librarian-survey.md`). The
  `pages_skimmed` / `pages_promoted` telemetry fields are the tuning data for
  the 15 cutoff.
