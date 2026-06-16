#!/usr/bin/env python3
"""Claim-level cascade: the evidence-chain blast radius of a source.

`/wiki` claim-provenance bundle, Slice A3 (Phase 2, work-stream 1). The
inverted `trace_claim`: where A1/A2 answer *"where did this claim come from?"*,
this answers *"if that source is refuted, what else rests on it?"* — closing
[[llm-wiki-failure-modes]] #4 (update cascade) at claim granularity.

The cascade unit is the **evidence chain** — claims that share a source — NOT
the wikilink graph. Session 107's S5 specimen is the load-bearing proof: the
[[kaitai-dong]] BlackRock identity claim rests on a LinkedIn profile, so its
blast radius is the *other bio claims on the same page that cite that profile*
— and touches NONE of the six pages that wikilink to kaitai-dong (those rest on
the AI Advances byline, a different source). A page-level `sources:`-intersection
on a multi-source page would pull every one of those six in as a false positive,
which is precisely why this module attributes each claim to a *specific* source
rather than to the page's whole source set.

Claim → source attribution, in decreasing-precision order (first hit wins):
  1. INLINE      — an adjacent quote blockquote ending `— [[source-slug]]`
                   (how entity-page claims are attributed in practice; the most
                   explicit signal). Scanned in a small window after the claim.
  2. VERBATIM    — the claim text appears verbatim in a cited source (delegated
                   to `trace_claim.trace`, intersected with the page's declared
                   `sources:` so a coincidental hit in an uncited file is ignored).
  3. SINGLE-SRC  — the claim's page lists exactly one source; the claim trivially
                   rests on it.
  4. UNATTRIBUTABLE — a synthesized claim on a multi-source page with no inline
                   attribution. We DO NOT fall back to the page's source set
                   (that is the S5 trap); instead we report the claim with an
                   empty blast radius and a nudge to anchor / attribute it.

Once a claim's source S is pinned, the blast radius is every page that lists S
in its `sources:` — refined, for each such page, to the anchored claims that
verbatim-trace to S, plus a count of unanchored `*[AMBIGUOUS]*` claims that *may*
rest on S (the nudge to anchor them for a precise cascade).

This module is a library first: `lint_wiki.check_cascade` is the shipped surface
(A3 v1). The on-demand `/wiki-trace --cascade` query surface is deferred until
demand surfaces (incremental-slice lock). A minimal `main()` is provided for
live verification / debugging only — it is not a user-facing skill.

Cost note: each at-risk anchored claim costs one `trace_claim.trace` vault walk.
Anchors are opt-in and deliberately sparse, so the per-lint cost is bounded; if
anchor density ever grows large, batch the vault read once across claims.

Usage (debug):
    python cascade_claim.py <wiki-root> [--vault DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import trace_claim

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# `{#claim-<slug>}` anchor (A2). Mirrors lint_wiki.CLAIM_ID_RE — the well-formed
# strict form; malformed anchors are a separate lint (`claim_id_issues`).
CLAIM_ID_RE = re.compile(r"\{#(claim-[a-z0-9-]+)\}")
# Inline attribution: a quote/line ending in `— [[source-slug]]` (em-dash, en-dash,
# or one/two hyphens). Anchors at end-of-line so a mid-sentence wikilink is not
# mistaken for the claim's source attribution.
ATTRIB_RE = re.compile(
    r"[—–-]{1,2}\s*\[\[\s*([^\]|#]+?)\s*(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]\s*$"
)
AMBIGUOUS_MARKER_RE = re.compile(r"\*\[AMBIGUOUS\]\*")
CONTRADICTION_CALLOUT = "> [!WARNING] Contradiction Detected:"
# How many lines after the claim line to scan for an inline attribution blockquote.
ATTRIB_WINDOW = 2


def source_slugs(frontmatter: dict) -> set[str]:
    """Slugs (filename stems) of a page's `sources:` frontmatter entries.

    `sources:` carries project-root paths (`vault/Web Articles/<slug>.md`); the
    stem is the stable identity used everywhere else (it matches `trace`'s
    `slug` field and survives the non-canonical clipper filenames).
    """
    srcs = frontmatter.get("sources") or []
    if not isinstance(srcs, list):
        return set()
    return {Path(s).stem for s in srcs if isinstance(s, str) and s.strip()}


def iter_anchored_claims(body: str):
    """Yield (claim_id, line_no, claim_text) for each `{#claim-id}` in `body`.

    `line_no` is 1-based. `claim_text` is the sentence terminating at the anchor,
    with the trailing `*[TAG]*` marker and leading list/blockquote noise stripped
    (delegated to `trace_claim.extract_claim_sentence`).
    """
    for i, line in enumerate(body.splitlines(), 1):
        m = CLAIM_ID_RE.search(line)
        if not m:
            continue
        claim_text = trace_claim.extract_claim_sentence(line[: m.start()])
        yield m.group(1), i, claim_text


def inline_attribution(body_lines: list[str], claim_line_no: int) -> str | None:
    """The source slug an inline quote-attribution pins the claim to, or None.

    Scans the claim's own line and the next `ATTRIB_WINDOW` lines for a trailing
    `— [[slug]]`. Stops at the first blank line (a blank ends the claim's block).
    """
    end = min(len(body_lines), claim_line_no + ATTRIB_WINDOW)
    for idx in range(claim_line_no - 1, end):
        line = body_lines[idx]
        if idx != claim_line_no - 1 and line.strip() == "":
            break
        m = ATTRIB_RE.search(line.rstrip())
        if m:
            return m.group(1).strip()
    return None


def attribute_claim(
    pages: dict, vault_root: Path | None, page_slug: str, claim_line_no: int, claim_text: str
) -> dict:
    """Pin a claim to its source slug(s). Returns {sources: set, provenance: str}.

    Precedence: inline attribution → verbatim trace ∩ page sources → single-source
    page → unattributable. Never falls back to a multi-source page's full source
    set (the S5 false-positive trap).
    """
    info = pages.get(page_slug, {})
    page_sources = source_slugs(info.get("frontmatter", {}))
    body_lines = info.get("body", "").splitlines()

    inline = inline_attribution(body_lines, claim_line_no)
    if inline:
        return {"sources": {inline}, "provenance": "inline-attribution"}

    if vault_root is not None and vault_root.is_dir() and claim_text:
        res = trace_claim.trace(vault_root, claim_text)
        if res["match_type"] == "verbatim":
            matched = {r["slug"] for r in res["results"]}
            pinned = (matched & page_sources) or matched
            return {"sources": pinned, "provenance": "verbatim"}

    if len(page_sources) == 1:
        return {"sources": set(page_sources), "provenance": "single-source-page"}

    return {"sources": set(), "provenance": "unattributable"}


def dependents_of_source(
    pages: dict, vault_root: Path | None, source_slug: str
) -> list[dict]:
    """Pages whose `sources:` includes `source_slug` — the evidence-chain.

    For each such page, refine to claim granularity: list the anchored claims
    that pin to `source_slug` (via `attribute_claim`), and count the unanchored
    `*[AMBIGUOUS]*` claims that may also rest on it (the anchoring nudge).
    """
    out: list[dict] = []
    for slug, info in sorted(pages.items()):
        # Source summary pages are the raw-citation transit layer — they *are*
        # the source, not a claim resting on it. Exempt them (consistent with
        # every other claim-tag check), or the source's own summary page would
        # always show up as its own dependent.
        if info.get("type") == "sources":
            continue
        if source_slug not in source_slugs(info["frontmatter"]):
            continue
        body = info["body"]
        anchored = []
        anchored_ids = set()
        for cid, line_no, claim_text in iter_anchored_claims(body):
            attr = attribute_claim(pages, vault_root, slug, line_no, claim_text)
            if source_slug in attr["sources"]:
                anchored.append({"claim_id": cid, "line": line_no})
                anchored_ids.add(cid)
        ambiguous_total = len(AMBIGUOUS_MARKER_RE.findall(body))
        out.append(
            {
                "page": info["path"],
                "slug": slug,
                "anchored_claims": anchored,
                # AMBIGUOUS markers not already accounted for by an anchored claim
                # that pinned to this source — the "may also rest on it" nudge.
                "unanchored_ambiguous": max(0, ambiguous_total - len(anchored)),
            }
        )
    return out


def at_risk_reasons(info: dict, claim_line: str) -> list[str]:
    """Why a claim is in the cascade review queue, or [] if it is not at risk.

    A claim is at risk when something signals its source may not hold:
    page `verify: true`, the claim itself tagged AMBIGUOUS/INFERRED, or the
    page carries a contradiction (typed edge or inline callout).
    """
    fm = info["frontmatter"]
    body = info["body"]
    reasons: list[str] = []
    if str(fm.get("verify", "")).lower() == "true":
        reasons.append("page verify: true")
    if "*[AMBIGUOUS]*" in claim_line:
        reasons.append("claim *[AMBIGUOUS]*")
    elif "*[INFERRED]*" in claim_line:
        reasons.append("claim *[INFERRED]*")
    if fm.get("contradicts") or CONTRADICTION_CALLOUT in body:
        reasons.append("page contradicted")
    return reasons


def cascade(pages: dict, vault_root: Path | None) -> list[dict]:
    """Compute the cascade review queue: each at-risk anchored claim + its
    evidence-chain blast radius.

    Returns a list of `{page, slug, claim_id, line, at_risk, provenance,
    sources, blast_radius}` rows, sorted by (page, claim_id). Only anchored,
    at-risk claims appear — the opt-in anchor set keeps this a sparse, low-noise
    queue (like `needs_verification`), not a whole-wiki report.
    """
    rows: list[dict] = []
    for slug, info in pages.items():
        body = info["body"]
        body_lines = body.splitlines()
        for cid, line_no, claim_text in iter_anchored_claims(body):
            claim_line = body_lines[line_no - 1] if line_no - 1 < len(body_lines) else ""
            reasons = at_risk_reasons(info, claim_line)
            if not reasons:
                continue
            attr = attribute_claim(pages, vault_root, slug, line_no, claim_text)
            srcs = sorted(attr["sources"])
            blast = []
            for s in srcs:
                deps = dependents_of_source(pages, vault_root, s)
                # Drop the claim's own anchor from its own blast radius — it is
                # the seed, not a dependent — but keep sibling claims/pages.
                blast.append({"source": s, "dependents": deps})
            rows.append(
                {
                    "page": info["path"],
                    "slug": slug,
                    "claim_id": cid,
                    "line": line_no,
                    "at_risk": reasons,
                    "provenance": attr["provenance"],
                    "sources": srcs,
                    "blast_radius": blast,
                }
            )
    rows.sort(key=lambda r: (r["page"], r["claim_id"]))
    return rows


def _dependent_count(row: dict) -> int:
    """Total pages in a row's blast radius (deduped across its sources)."""
    pages_hit: set[str] = set()
    for b in row["blast_radius"]:
        for d in b["dependents"]:
            pages_hit.add(d["slug"])
    return len(pages_hit)


# ---------- debug CLI (not a user-facing surface) ----------
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Claim-level cascade (debug).")
    parser.add_argument("wiki_root", type=Path)
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    wiki_root = args.wiki_root.resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} is not a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2
    vault_root = (
        args.vault.resolve() if args.vault else (wiki_root.parent / "vault")
    )
    if not vault_root.is_dir():
        vault_root = None

    # Lazy import to avoid a module-level cycle (lint_wiki imports this module).
    import lint_wiki

    pages = lint_wiki.collect_pages(wiki_root)
    rows = cascade(pages, vault_root)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No at-risk anchored claims — cascade queue empty.")
        return 0
    for r in rows:
        print(f"{r['page']}  {{#{r['claim_id']}}}  (line {r['line']})")
        print(f"  at risk: {', '.join(r['at_risk'])}")
        print(f"  source ({r['provenance']}): {', '.join(r['sources']) or '— unattributable'}")
        for b in r["blast_radius"]:
            for d in b["dependents"]:
                anchored = ", ".join(f"{{#{c['claim_id']}}}" for c in d["anchored_claims"])
                extra = f"; +{d['unanchored_ambiguous']} unanchored AMBIGUOUS" if d["unanchored_ambiguous"] else ""
                print(f"    rests on {b['source']}: {d['page']} [{anchored or 'page-level'}{extra}]")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
