---
name: wiki-trace
description: Trace a wiki claim back to the exact raw-vault passage it came from — quote-level provenance. Deterministic stdlib output (no librarian, no model call). Use when the user runs /wiki-trace, asks "where did this claim come from", "show the source text for this claim", "trace this quote", or doubts a specific wiki assertion and wants the underlying source span rather than the whole source file. Accepts a literal quote OR a `[[page-slug#claim-id]]` anchor (claim-IDs, slice A2).
argument-hint: "<quote | [[page-slug#claim-id]]> [--max-results N] [--min-score F] [--strong-score F]"
---

# wiki-trace

Quote-level provenance lookup. Takes a phrase or claim lifted from a compiled wiki page and returns the raw-vault passage it was EXTRACTED from — surrounding paragraph + filename + line. Wraps `${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py`.

Closes the Mode 3 (truth compression) gap: page-level `sources:` frontmatter tells you *which files* a page drew on, but a reader doubting one sentence currently has to re-read the whole source. `/wiki-trace` resolves a single claim to a single span.

Two-tier match (paragraph granularity, over `vault/`):
- **Verbatim** — the claim, whitespace-normalized, is a substring of a vault paragraph. True provenance: the claim was EXTRACTED directly from that span.
- **Fuzzy fallback** (only when no verbatim hit) — lightweight stdlib token-overlap surfaces the closest paraphrase candidates. **Not exact provenance** — the output labels these as candidates, not proof. Candidates are **score-tiered** (trace v1.2): a high-containment outlier (score ≥ `--strong-score`, default 0.75) renders as a **likely reformatted verbatim home** — the claim probably IS extracted, and the compiler reformatted it past what `normalize()` folds — while a flat list near the 0.5 floor renders as **topical noise**, consistent with a genuinely synthesized INFERRED/AMBIGUOUS claim that has no single verbatim home.

Searches the raw vault, excluding any path whose first segment starts with `.` or `_` (the canonical ingest-exclusion rule). Pure script output — no librarian, no synthesis, no token cost beyond this skill's prompt.

*Architecturally: Option C (inline tool-orchestration) per `_template-simple/SKILL.md`. No subagent dispatch. Slices A1+A2 of the claim-provenance bundle — input is a literal quote (A1) or a `[[page-slug#claim-id]]` anchor (A2: the skill resolves the anchor to the claim text on the page, then traces that into the vault). Claim→source mapping + the dependency-graph cascade are A3.*

## Workflow

### 1. Locate the wiki and vault
Find the wiki root in the current directory or upward (look for `wiki/CLAUDE.md`). The raw vault is its sibling `vault/` directory — that is the search target (provenance traces *into the raw sources*, not the compiled wiki).

### 2. Get the claim to trace
Two input forms:
- **Literal quote or claim sentence** (A1). Accept it verbatim; the script strips surrounding quotes. If the user gives a wiki page + a vague "trace this page," ask which specific claim — `/wiki-trace` resolves a *span*, not a whole page.
- **`[[page-slug#claim-id]]` anchor** (A2). When a claim on a wiki page carries a `{#claim-<slug>}` anchor, the user can reference it directly. The script resolves the anchor to the claim text on the page, then traces that text into the vault — no copy-paste needed, and the reference is stable across edits.

### 3. Run trace_claim.py
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py" vault "<quote | [[page-slug#claim-id]]>"
```

Examples:
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py" vault "the harness matters more than the model"
python "${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py" vault "markdown beat the vector database" --max-results 3
# Anchor input (A2) — pass --wiki so the page can be located for resolution:
python "${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py" vault "[[claude-code#claim-cc-agent-teams-2026]]" --wiki wiki
```

For anchor input, pass `--wiki <wiki-root>` so the page can be found under `wiki/pages/**`. (Without it the script falls back to the sibling `wiki/` dir of the vault root, which works for the standard layout but is explicit when passed.) Add `--json` for machine-readable output / downstream chaining (fuzzy payloads carry a per-result `tier` plus a `fuzzy_tiers` summary). Lower `--min-score` (default 0.5) to widen the fuzzy net when a verbatim trace misses; adjust `--strong-score` (default 0.75) to move the likely-verbatim-home tier boundary.

### 4. Show the result to the user
Pass the script's output through verbatim — do not synthesize prose around it. The script's output IS the answer. Vault paths (`` `Web Articles/...md` ``) are the locator, not `[[wikilinks]]` (these resolve into the raw vault, not the wiki graph).

Interpret the match type when handing back:
- **Verbatim** → "Found the source span: …" — this is exact provenance.
- **Fuzzy, with a likely-verbatim-home tier** → a high-score outlier means the claim was probably EXTRACTED and reformatted; invite the user to read the surfaced span to confirm it — still a candidate, not proof.
- **Fuzzy, topical-only** → a flat candidate list with no outlier means the claim is likely INFERRED/AMBIGUOUS (synthesized) — point the user at the page's `sources:` frontmatter for the full provenance set.
- **None** → the claim has no verbatim or close vault span; it's synthesized. Surface the page's `sources:` as the provenance of record.

## Rules

- **Read-only.** Never modify wiki pages or vault sources. The vault is immutable.
- **No librarian, no model call.** Trace is deterministic stdlib; do not dispatch a subagent.
- **No synthesis.** The script's output is the answer. The user asked "where did this come from," not for a rewrite.
- **Verbatim wins outright.** When any verbatim span exists, fuzzy candidates are suppressed — don't second-guess an exact match with paraphrase noise.
- **Fuzzy ≠ provenance.** Never present a fuzzy candidate as the confirmed source — including a `likely_verbatim_home` one; the tier is a reading aid, and confirmation is reading the span. Label it a candidate and defer to `sources:` frontmatter.
- **No edits to log.md.** Trace is a read operation; no `## [<timestamp>]` log entry.
- **Telemetry still deferred (A1+A2).** Neither slice writes a `query_log.jsonl` row — A2 was scoped no-schema-bump (claim-IDs are opt-in/body-only, same non-breaking shape as the claim-tag markers), so the `append_query_log` value addition was held back too. Read-side trace telemetry (a `trace` query-skill row feeding `/wiki-ask` router design) lands when the `/wiki-ask` work needs it — see `dev/2026-06-15-claim-provenance-v1-design.md`.
- **Anchor resolution is best-effort, not provenance by itself.** `[[page#claim-id]]` resolves to the claim *text*, which is then traced exactly like a pasted quote — the anchor is an input convenience, not a recorded claim→source link. Claim→source mapping is A3 (still open).
