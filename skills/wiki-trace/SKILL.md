---
name: wiki-trace
description: Trace a wiki claim back to the exact raw-vault passage it came from — quote-level provenance. Deterministic stdlib output (no librarian, no model call). Use when the user runs /wiki-trace, asks "where did this claim come from", "show the source text for this claim", "trace this quote", or doubts a specific wiki assertion and wants the underlying source span rather than the whole source file.
argument-hint: "<quote or claim> [--max-results N] [--min-score F]"
---

# wiki-trace

Quote-level provenance lookup. Takes a phrase or claim lifted from a compiled wiki page and returns the raw-vault passage it was EXTRACTED from — surrounding paragraph + filename + line. Wraps `${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py`.

Closes the Mode 3 (truth compression) gap: page-level `sources:` frontmatter tells you *which files* a page drew on, but a reader doubting one sentence currently has to re-read the whole source. `/wiki-trace` resolves a single claim to a single span.

Two-tier match (paragraph granularity, over `vault/`):
- **Verbatim** — the claim, whitespace-normalized, is a substring of a vault paragraph. True provenance: the claim was EXTRACTED directly from that span.
- **Fuzzy fallback** (only when no verbatim hit) — lightweight stdlib token-overlap surfaces the closest paraphrase candidates. **Not exact provenance** — INFERRED/AMBIGUOUS claims are synthesized across sources and have no single verbatim home; the output labels these as candidates, not proof.

Searches the raw vault, excluding any path whose first segment starts with `.` or `_` (the canonical ingest-exclusion rule). Pure script output — no librarian, no synthesis, no token cost beyond this skill's prompt.

*Architecturally: Option C (inline tool-orchestration) per `_template-simple/SKILL.md`. No subagent dispatch. Slice A1 of the claim-provenance bundle — input is a literal quote; `[[page#claim-id]]` anchor input arrives with claim-IDs in slice A2.*

## Workflow

### 1. Locate the wiki and vault
Find the wiki root in the current directory or upward (look for `wiki/CLAUDE.md`). The raw vault is its sibling `vault/` directory — that is the search target (provenance traces *into the raw sources*, not the compiled wiki).

### 2. Get the claim to trace
The argument is a quote or claim sentence. Accept it verbatim. If the user pasted it with surrounding quotes, the script strips them. If the user gives a wiki page + a vague "trace this page," ask which specific claim — `/wiki-trace` resolves a *span*, not a whole page.

### 3. Run trace_claim.py
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py" vault "<quote or claim>"
```

Examples:
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py" vault "the harness matters more than the model"
python "${CLAUDE_PLUGIN_ROOT}/scripts/trace_claim.py" vault "markdown beat the vector database" --max-results 3
```

Add `--json` if the user asked for machine-readable output or downstream chaining wants structured fields. Lower `--min-score` (default 0.5) to widen the fuzzy net when a verbatim trace misses.

### 4. Show the result to the user
Pass the script's output through verbatim — do not synthesize prose around it. The script's output IS the answer. Vault paths (`` `Web Articles/...md` ``) are the locator, not `[[wikilinks]]` (these resolve into the raw vault, not the wiki graph).

Interpret the match type when handing back:
- **Verbatim** → "Found the source span: …" — this is exact provenance.
- **Fuzzy** → make clear these are *paraphrase candidates*, not proof. The claim is likely INFERRED/AMBIGUOUS (synthesized) — point the user at the page's `sources:` frontmatter for the full provenance set.
- **None** → the claim has no verbatim or close vault span; it's synthesized. Surface the page's `sources:` as the provenance of record.

## Rules

- **Read-only.** Never modify wiki pages or vault sources. The vault is immutable.
- **No librarian, no model call.** Trace is deterministic stdlib; do not dispatch a subagent.
- **No synthesis.** The script's output is the answer. The user asked "where did this come from," not for a rewrite.
- **Verbatim wins outright.** When any verbatim span exists, fuzzy candidates are suppressed — don't second-guess an exact match with paraphrase noise.
- **Fuzzy ≠ provenance.** Never present a fuzzy candidate as the confirmed source. Label it a candidate and defer to `sources:` frontmatter.
- **No edits to log.md.** Trace is a read operation; no `## [<timestamp>]` log entry.
- **Telemetry deferred (A1).** Slice A1 ships zero schema change, so `/wiki-trace` does not yet write a `query_log.jsonl` row. Read-side telemetry (a `trace` query-skill row feeding `/wiki-ask` router design) lands with slice A2's schema touch — see `dev/2026-06-15-claim-provenance-v1-design.md`.
