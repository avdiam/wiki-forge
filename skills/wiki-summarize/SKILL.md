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

Record the main-session wall-clock start time before retrieval — the telemetry
row in step 6 needs `duration_ms` even if the librarian's `<usage>` is missing
or malformed.

### 3. Two-shape retrieval

Build the wikilink graph and try slug resolution first; fall through to fuzzy
search on miss.

**Topic-passing convention.** `<topic>` is captured into a shell variable via a
single-quoted heredoc (no expansion, opaque to bash) and passed to Python via
`argv`. This survives `"`, `\`, `$`, backticks, apostrophes, and embedded
newlines without escaping. Replace `<topic>` literally — do not pre-escape.

```bash
WIKI_SUMMARIZE_TOPIC=$(cat <<'TOPIC_EOF'
<topic>
TOPIC_EOF
)
python - "$WIKI_SUMMARIZE_TOPIC" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "<wiki>/.scripts")
from graph_analyzer import build_graph
from graph_query import _resolve_slug, neighbors_within

raw = sys.argv[1]
wiki = Path("<wiki>")
nodes, outbound, inbound = build_graph(wiki)
adj = {n: outbound.get(n, set()) | inbound.get(n, set()) for n in nodes}

slug = _resolve_slug(raw, nodes)
if slug in nodes:
    # Map slugs → relative paths once, so the librarian receives paths
    # directly (symmetric with /wiki-query Path B's hybrid_search.py output).
    slug_to_path = {}
    for type_dir in ("concepts", "entities", "sources", "comparisons", "syntheses"):
        for p in (wiki / type_dir).glob("*.md"):
            slug_to_path[p.stem] = f"{type_dir}/{p.name}"

    pairs = neighbors_within(adj, slug, 1)
    pages = [slug] + [s for s, _ in pairs]
    inbound_count = len(inbound.get(slug, set()))
    print("SHAPE_A", slug, len(pages), inbound_count)
    for s in pages:
        print(slug_to_path.get(s, f"# UNRESOLVED:{s}"))
else:
    print("SHAPE_B")
PY
```

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
- Page paths are resolved by the bash glue (`slug_to_path` dict scans the five
  canonical type-dirs once); the librarian receives paths directly, no Glob
  round-trips needed.
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

Read these specific pages in full and produce the five-section
topic-coverage survey per the survey-mode block in your playbook
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

### 6. Log query telemetry

Write one row to `wiki/.meta/query_log.jsonl`. Field values:

- `--query-skill summarize`
- `--query "<raw user topic>"`
- `--search-mode <slug-anchored|hybrid>` (Shape A → `slug-anchored`; Shape B → `hybrid`)
- `--pages-consulted` — parse from the librarian's footer `Pages consulted: [[…]]` via regex `\[\[([^\]]+)\]\]` (same as `/wiki-query` Path B)
- `--top-score` — Shape B: top result's normalized `score` from `hybrid_search.py` JSON; Shape A: `""` (no search score)
- `--filed-as` — parse from librarian's `Filed as:` footer line (slug or `Not filed` → null)
- Cost fields — pipe the librarian's `<usage>` block into `parse_agent_usage.py` (same command as `/wiki-query` step 6, with `--agent-id` and `--out .claude/tmp/usage-summarize.json`) and pass `--usage-json`. Never regex the block by hand.
- `--duration-ms` — only when no `<usage>` block arrived: the main-session wall-clock (overrides the file).
- `--query-variations ""` — `/wiki-summarize` does not expand queries (single-topic surveys).
- If this invocation was routed from `/wiki-ask`, add `--routed-from ask`.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/append_query_log.py" --wiki <wiki> \
  --query-skill summarize \
  --query "<raw topic>" \
  --search-mode <slug-anchored|hybrid> \
  --pages-consulted "<comma-sep slugs>" \
  --top-score <float or ""> \
  --filed-as "<synthesis-slug or "">" \
  --usage-json .claude/tmp/usage-summarize.json
```

Telemetry is observational — if the appender exits non-zero, mention it briefly
in the relay but do NOT retry and do NOT fail the survey.

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
