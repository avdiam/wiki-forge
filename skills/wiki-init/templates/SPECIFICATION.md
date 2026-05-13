---
schema_version: 2
not_a_source: true
last_updated: 2026-05-13
---

# Wiki — Script-API Specification

> **This file is the script-API reference for `wiki/.scripts/`.** Not auto-loaded; not read by any agent at runtime. The relevant scripts encode these contracts more precisely (argparse, docstrings, `--help`). Preserved during the 2026-05-13 schema decomposition as a conservative safety net for ~330 lines of script-behavior documentation that had no agent / skill / script runtime consumer. **Marked for review in 3–6 months — if no one consults it, delete outright.** See [`../dev/wiki-agents.md-refactor.md`](../dev/wiki-agents.md-refactor.md) for the decomposition decision and [`../dev/tech-debt.md`](../dev/tech-debt.md) for the review-trigger entry.

This file contains:

- Helper-script behavior — semantic search, graph analysis
- Sidecar config schemas — `.meta/lint-config.json`, `.meta/secret-patterns.json`
- Telemetry — the six append-only JSONL logs under `.meta/` (ingest, query, lint, overview, source-freshness, corroborate)

---

## Semantic search (optional)

If `.scripts/hybrid_search.py` and `.scripts/models/` are present, the
wiki-query skill can use hybrid BM25+semantic search for page discovery.
This is only used for query answering, never for ingestion (the ingestor
uses grep for speed and zero-dependency operation).

The search is conditional: for small wikis (under ~6000 index tokens,
roughly ~90 pages at typical entry density), the query skill reads the
sub-indices directly. Semantic search activates automatically as the wiki
grows past that point.

To set up: `python .scripts/setup_search.py`
To disable: delete `.scripts/models/`

## Graph analysis

`.scripts/graph_analyzer.py` analyzes the wikilink topology — hubs (most-
linked pages), sinks (dead ends), orphans, and connected components. The
wiki-linter runs this during Pass A to detect structural problems that
page-level checks miss (e.g., isolated clusters of pages that should be
cross-referenced).

## Lint configuration (optional) — `.meta/lint-config.json`

`lint_wiki.py` reads this sidecar (when present) to gate the
`artifact_presence` check. The check flags pages that lack any structural
artifact — fenced code blocks, GFM tables, numbered procedures (≥2 entries),
attributed block quotes (`> "…"`), or definition lists (`**term** — defn`,
≥2 entries). The patterns are domain-agnostic; what differs across wikis is
which page categories *should* expect them.

Schema: `{"artifact_check_categories": ["cat-a", "cat-b", ...]}`. Empty or
missing allowlist → check is silent (default for fresh wikis). Source-summary
pages are always excluded — they're the raw-citation transit layer, not
synthesized content. Soft-warning severity; never blocking.

Cross-domain rationale: a philosophy or history wiki may legitimately have
pure-prose concept pages where requiring code blocks or tables would be
wrong. The opt-in mechanism keeps the check useful for technical wikis
without forcing IT-flavor conventions on every domain. Per-page override
via a `has_artifact: true | false` frontmatter flag is a deferred follow-on;
revisit if the per-category mechanism shows real false-positive volume.

## Secret-pattern config (optional) — `.meta/secret-patterns.json`

`wiki/.scripts/check_secrets.py` ships with a built-in pattern set
covering the highest-volume credential prefixes — GitHub PATs (classic /
oauth / fine-grained), AWS access keys, Stripe live + test secrets,
OpenAI keys, Anthropic keys, Google API keys, Slack tokens — plus a
soft-warning pass over high-entropy strings (≥40 chars, mixed
letter+digit) inside fenced code blocks. The scanner runs at
`/wiki-ingest` step 10.5 against the source file before the takeaway
gate; HALT semantics on high findings, soft warning on low.

Schema: `{"<pattern_name>": "<python_regex>", ...}` — extends (does not
replace) the built-ins. Each value is compiled at scan time with
`re.compile`; invalid patterns are skipped with a stderr warning rather
than crashing the scan.

Use cases: pinning a custom corporate-domain auth-token format,
suppressing a known false-positive class via more specific replacement
of the built-in (define a same-named entry to override), or adding
emerging credential prefixes from new SaaS integrations. Keep additions
narrow — broad regexes degrade the signal-to-noise of HALT severity.

The scanner is the parser-side application of the [[lethal-trifecta]]
"sanitize the untrusted-content leg" mitigation: categorically reliable
for known patterns, no model dependency. Pure stdlib; no `--json`
toggle (output is always JSON to stdout); exit code 0 / 1 / 2 mirrors
no findings / low only / any high.

## Telemetry (optional)

Six append-only JSONL logs under `.meta/` capture per-operation cost
and retrieval data — one per side-effecting operation (ingest, query,
lint, overview) plus one for source-freshness polling — enabling
empirical validation of the wiki pattern's
ingest-amortizes-through-query-reuse bet (see `[[llm-wiki-failure-modes]]`
section #6). On-demand analysis only — nothing runs automatically.

**`.meta/ingest_log.jsonl`** — one row per ingest. Written by `/wiki-ingest`
after the ingestor subagent returns; cost fields are parsed from the
`<usage>` block the Agent tool surfaces on subagent completion.

```json
{
  "timestamp": "2026-04-20T14:30:05",
  "source_path": "vault/Web Articles/2026-04-15-foo.md",
  "source_type": "article",
  "source_word_count": 3136,
  "pages_created": ["sources/2026-04-15-foo", "concepts/bar"],
  "pages_updated": ["entities/baz"],
  "contradictions_flagged": 0,
  "total_tokens": 158204,
  "input_tokens": 122,
  "cache_creation_input_tokens": 18432,
  "cache_read_input_tokens": 138210,
  "output_tokens": 1440,
  "tool_uses": 47,
  "duration_ms": 312405,
  "commit_hash": "a1b2c3d"
}
```

The four per-field token counts (`input_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`,
`output_tokens`) are surfaced by the Agent tool's `<usage>` block when
present and captured per-field — any subset may be `null` if the block
omits a field. `cache_creation_input_tokens` is the load-bearing slice
for measuring cache savings from auto-loaded context (e.g., the
SessionStart hook); `cache_read_input_tokens` is the cache-hit volume
that scales with replayed conversation context. `total_tokens` remains
the aggregate.

**`.meta/query_log.jsonl`** — one row per `/wiki-query`, `/wiki-graph`,
or `/wiki-summarize` invocation. The `query_skill` field distinguishes
the origins. `/wiki-query` writes on both handling paths: inline (small
wiki, no subagent) and librarian-dispatch (large wiki). `/wiki-graph`
writes one row per traversal (path / neighbors / subgraph) — pure script
output, no subagent, no model call. `/wiki-summarize` always dispatches
the librarian in survey-mode and writes a row structurally similar to
`/wiki-query` Path B.

```json
{
  "timestamp": "2026-04-20T14:31:12",
  "query_skill": "query",
  "query": "what is RAG",
  "query_variations": ["retrieval-augmented generation", "RAG pipeline"],
  "search_mode": "hybrid",
  "pages_consulted": ["concepts/rag", "concepts/llm-wiki-pattern"],
  "top_score": 0.82,
  "filed_as": null,
  "total_tokens": null,
  "input_tokens": null,
  "cache_creation_input_tokens": null,
  "cache_read_input_tokens": null,
  "output_tokens": null,
  "tool_uses": null,
  "duration_ms": 8100
}
```

`query_skill` values: `"query"` (default; `/wiki-query` invocations),
`"graph"` (`/wiki-graph` invocations), or `"summarize"` (`/wiki-summarize`
invocations). Forward-compatible with rows predating the field — the
absence of `query_skill` is treated as `"query"` by `query_stats.py`.

`search_mode` values: `"inline"` (Path A), `"hybrid" | "hybrid_zvec" |
"bm25_only"` (Path B retrieval modes from `hybrid_search.py`),
`"slug-anchored"` (`/wiki-summarize` Shape A — topic resolved to an
existing slug, pages from `graph_query.neighbors_within`), or `null`
(graph rows).

On Path A (`search_mode: inline`) all six cost fields are `null` — no
subagent is dispatched, so no `<usage>` metadata reaches the caller;
`duration_ms` (main-session wall-clock) is the Path-A cost proxy. On
Path B (`search_mode: hybrid | hybrid_zvec | bm25_only`), every cost
field is parsed independently from the librarian's `<usage>` block;
fields the block omits stay `null`.

For `query_skill: "graph"` rows, `search_mode` and all six cost fields
are `null` (no model call). `query` carries the raw subcommand + args
(e.g., `"neighbors lethal-trifecta --depth 2"`); `pages_consulted`
carries the result set (path slugs / neighbor slugs / subgraph nodes);
`duration_ms` is the script wall-clock from `graph_query.py`'s JSON output.

For `query_skill: "summarize"` rows, structure mirrors `query` Path B —
the librarian is always dispatched in survey-mode, so cost fields are
parsed from its `<usage>` block. `search_mode` is `"slug-anchored"` for
Shape A (topic resolves to an existing slug; pages = anchor +
`neighbors_within(depth=1)`) or `"hybrid"` for Shape B (fuzzy fallback;
pages = `hybrid_search.search` top-k=25 results). `query` carries the
raw user topic; `filed_as` records the coverage-report synthesis slug
(`syntheses/<slug>-coverage`) if filed, else `null`. Empty Shape B
results write no row — the skill emits the dual-onramp inline without
dispatching the librarian.

`query_variations` (Phase 2 — multi-query expansion) is the JSON array of
paraphrase variations the `/wiki-query` skill generated and passed to
`hybrid_search.py`'s `--queries-json` flag, *excluding* the canonical
first query (which lives in `query`). `null` on Path A (no expansion),
on N=1 Path B, and on all `query_skill: "graph"` rows.

**`.meta/lint_log.jsonl`** — one row per lint run. Written from two paths:
(a) the post-commit mechanical lint the ingestor runs on every ingest, and
(b) the full three-pass `/wiki-lint` run. The `lint_type` field
distinguishes them.

```json
{
  "timestamp": "2026-04-21T14:32:04",
  "lint_type": "full",
  "ingest_count_at_run": 27,
  "total_pages": 93,
  "counts": {
    "broken_links": 0, "orphans": 2, "missing_frontmatter": 0,
    "stale": 0, "low_confidence": 1, "index_drift": 0,
    "source_backlog": 0, "stale_source": 0,
    "overview_staleness": 0, "single_page_categories": 1,
    "long_summary": 0, "needs_verification": 3,
    "excessive_ambiguous": 0,
    "unacknowledged_contradictions": 0, "category_issues": 0
  },
  "verdict": "issues",
  "semantic_pass_ran": true,
  "total_tokens": 48210,
  "input_tokens": 95,
  "cache_creation_input_tokens": 6120,
  "cache_read_input_tokens": 41610,
  "output_tokens": 385,
  "tool_uses": 18,
  "duration_ms": 184500,
  "commit_hash": "a1b2c3d"
}
```

`lint_type: mechanical` rows have `semantic_pass_ran: false`, all six
cost fields null (stdlib script, no subagent), and typically
`commit_hash: null` (mechanical runs don't commit on their own).
`lint_type: full` rows have `semantic_pass_ran: true` and cost fields
populated per-field from the linter subagent's `<usage>` block; missing
sub-fields stay null. `commit_hash` is populated only if the user
approved fixes that produced a commit. `ingest_count_at_run` is a
monotonic snapshot — the count of ingest entries in `log.md` at the
moment lint fired — and is the anchor for "ingests since last full lint"
derivations (current ingest count minus max `ingest_count_at_run` over
rows with `lint_type: full`).

**`.meta/overview_log.jsonl`** — one row per `/wiki-overview` invocation,
written from Mode A (soft refresh) and Mode B (full rewrite) on curator
return.

```json
{
  "timestamp": "2026-05-07T14:30:21",
  "mode": "soft",
  "trigger": null,
  "ingest_count_at_run": 120,
  "status": "applied",
  "total_tokens": 28100,
  "input_tokens": 60,
  "cache_creation_input_tokens": 4200,
  "cache_read_input_tokens": 22300,
  "output_tokens": 540,
  "tool_uses": 9,
  "duration_ms": 41200,
  "commit_hash": "a1b2c3d"
}
```

`mode: soft` rows carry `trigger: null` (soft refresh has no trigger —
it's the cadence-driven bookkeeping pass). `mode: full` rows carry the
trigger reasons string from `check_overview_triggers.py` or the literal
`"user-invoked"` when the user passed `--full`. `status` is `applied`
when the dispatch produced a commit, `no_changes` when Mode A's diff
came up empty (skip-the-commit path), or `cancelled` when Mode B's
Pass 2 returned `status: cancelled` (user rejected the rewrite at the
approval gate). `commit_hash` is null on `no_changes` / `cancelled`.

Mode B emits two `<usage>` blocks (one per pass). The skill aggregates
across both passes for the row's cost fields and `duration_ms` per the
file-checkpoint HITL contract; `commit_hash` is from the Pass-2 commit
only.

**`.meta/source_freshness.jsonl`** — one row per source-page poll,
written by `wiki/.scripts/check_source_freshness.py` (phase 2 of the
[[llm-wiki-failure-modes]] #8 mitigation). Each poll establishes or
checks the slug's content-drift baseline.

```json
{
  "polled_at": "2026-05-07T14:35:12",
  "slug": "2025-11-25-writing-a-good-claude-md-humanlayer",
  "url": "https://www.humanlayer.dev/blog/writing-a-good-claude-md",
  "http_status": 200,
  "content_hash": "a1b2c3d4e5f6a7b8",
  "baseline_hash": "a1b2c3d4e5f6a7b8",
  "drifted": false,
  "error": null,
  "reason": null
}
```

`http_status` is the upstream HTTP code on a successful fetch (`200`,
`404`, …) OR a string sentinel for skipped / failed polls:

- `"skipped"` with `reason: "requires_clipper"` — paywall domain
  (Medium and known mirrors); phase 3 wires these to clipper.
- `"skipped"` with `reason: "no_url"` or `"no_vault_link"` — source
  page has no resolvable upstream URL; nothing to poll.
- `"error"` with `error: "timeout" | "dns" | "connection" | …` —
  network failure after 1 retry; row is recorded so the failure is
  audit-visible.

`content_hash` is `sha256(extracted_text)[:16]` — same 16-hex shape as
`source_hash:` for visual parity but hashing different bytes (extracted
main content via stdlib `html.parser`, with `<script>`/`<style>`/`<nav>`/
`<header>`/`<footer>`/`<aside>`/`<form>`/`<noscript>`/`<iframe>` stripped
and whitespace collapsed). Raw-HTML hashes are useless because most
pages mutate every load (timestamps, CSRF tokens, ad slots, A/B
variants); the extraction layer is what makes drift detection signal
rather than noise.

`baseline_hash` is the slug's first-ever `content_hash`. The first
successful poll for a slug establishes baseline (`baseline_hash ==
content_hash`, `drifted: false`). Every subsequent poll compares against
that first baseline; mismatch sets `drifted: true` and the polling
script edits the source page to add `source_drifted: true` frontmatter
plus an inline `> [!WARNING] Source drift detected` callout. The
`check_drifted_sources` lint check surfaces those flagged pages.
Drift-clear path: re-capture via clipper, ingestor regenerates the
source page from the fresh vault file, drift flag and callout disappear
naturally.

**`.meta/corroborate_log.jsonl`** — one row per `verify: true` flag the
`/wiki-corroborate` skill searched. Distinct from `query_log` because
the skill is search-and-surface (no synthesis), telemetry granularity is
per-flag (not per-invocation), and the row mutates twice after creation:
once at `/wiki-capture` time when the user picks a candidate URL, once
at `/wiki-ingest` time when the verify-clearing takeaway gate clears the
flag. The two backfills make the row a one-stop record of "flag → search
→ capture → clear" for the queue-drain.

```json
{
  "timestamp": "2026-05-15T14:30:21",
  "page_slug": "claude-managed-agents",
  "page_type": "entity",
  "page_bucket": "B",
  "callout_text": "the 100KB persistent-memory cap and...",
  "candidates_returned": 5,
  "candidates_by_tier": {"tier_1": 2, "tier_2": 2, "tier_3": 1},
  "candidate_domains": [
    {"domain": "docs.anthropic.com/claude", "tier": "tier_1"},
    {"domain": "github.com/anthropic", "tier": "tier_1"},
    {"domain": "theverge.com/2026", "tier": "tier_2"},
    {"domain": "bloomberg.com/news", "tier": "tier_2"},
    {"domain": "medium.com/@joeNjenga", "tier": "tier_3"}
  ],
  "include_tier_3_flag": false,
  "user_picked": "https://docs.anthropic.com/...",
  "user_action": "captured",
  "captured_to_vault": "vault/Web Articles/2026-05-15-...",
  "flag_cleared_at_ingest": "2026-05-15T14:55:11",
  "flag_decision_at_ingest": "Y",
  "total_tokens": null,
  "tool_uses": null,
  "duration_ms": null
}
```

`page_bucket` values follow the 2026-05-08 queue audit: `A` (entity bio
thinness — `category: ai-practitioners`), `B` (entity with specific
factual claims), `C` (concept / comparison / synthesis), `D` (source —
out of scope for `/wiki-corroborate` itself; reserved in the schema for
hypothetical downstream callers that target source-internal warnings).

`user_action` values: `deferred` (initial state — skill surfaced
candidates, user has not yet acted), `captured` (user ran `/wiki-capture`
on the picked URL — backfilled by the skill or by `/wiki-capture`), or
`skipped` (user explicitly passed on this batch's hits — reserved for
future interactive flow). `user_picked` and `captured_to_vault` are
populated only in the `captured` state.

`candidate_domains` (added v1.2) — list of `{domain, tier}` records, one
per filtered candidate the skill surfaced (post-filter, i.e. denylist
dropped and `tier_3` dropped unless `--include-tier-3` was passed).
Domains are extracted from URLs with host + first path segment
preserved when present (e.g., `github.com/anthropic`,
`linkedin.com/in/<user>`) so path-prefix tier patterns stay
distinguishable in analysis. Empty list `[]` means WebSearch failed or
every candidate was dropped; `null` means the row predates the field
(pre-v1.2 rows). Purpose: **seed-list backfill** — grouping
`unclassified` domains across many rows surfaces real-world host
patterns the `url_classifier_lists.json` seeds miss, so the cascade-
update rule can extend the lists at decision-gate time with data, not
speculation. Privacy posture: domain only; URL path tail / query
strings / fragments are dropped — no per-candidate forensic detail
beyond the path-prefix needed for tier matching.

`flag_decision_at_ingest` is backfilled by the wiki-ingestor on every
verify-clearing decision (Y, P, AND N) at the takeaway-approval gate —
captures the user's choice so per-decision telemetry analysis is
possible. Values: `"Y"` (clear), `"P"` (partial-narrow), `"N"`
(retain), or `null` (decision hasn't fired yet, or no corroborate
row exists for the touched page). Matches by `page_slug` + most-recent
row regardless of `user_action` state — parallel-path captures (e.g.,
the source surfaced via `/wiki-triage` instead of
`/wiki-corroborate` → `/wiki-capture`) still close the loop.

`flag_cleared_at_ingest` is the dependent stamp: set to the ISO
timestamp only on Y (full clear); left `null` on P / N / no-decision.
Invariant: `flag_cleared_at_ingest != null ⟺ flag_decision_at_ingest == "Y"`.
The two fields are intentionally paired — the timestamp answers "when
did the flag clear?" and the decision field answers "what did the user
decide on each touch?" Same shape as the `commit_hash` repair pattern
in `vault/.wiki-identity.md` § Telemetry repair.

Cost fields stay `null` in v1 — `/wiki-corroborate` is main-session
only (no subagent dispatched), and per the session 42 finding
`total_tokens` for main-session work isn't surfaced by the harness.
Reserved fields kept for forward symmetry with the other JSONL logs.

**Analysis:** `python .scripts/query_stats.py` reads the ingest and query
logs and reports page-hit frequency, exact-string + canonicalized query
repetition, dead pages (never consulted), and the bilateral amortization
ratio (cumulative query cost per page ÷ per-page ingest cost). Lint,
overview, source-freshness, and corroborate telemetry have no dedicated
stats script yet — query the JSONL directly with `jq` for ad-hoc
analysis.

**Commit policy.** All six JSONL files are committed to git — plain
text, inspectable, valuable as historical record at current scale.
Revisit if they grow noisy or start carrying sensitive queries.
