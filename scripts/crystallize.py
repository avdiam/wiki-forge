#!/usr/bin/env python3
"""Crystallization: turn a synthesis page's atomic facts into reinforcements of
the existing concept/entity graph.

The 4th canonical wiki operation (Ingest → Query → Summarize → **Crystallize** →
Lint), Phase 2 build-set stream #2. A `/wiki-query` synthesis page is a new leaf
node; crystallization lifts the standalone facts it surfaced and *merges them
into the existing pages that were missing them* — so the synthesis becomes a
write-side reinforcer of the prior knowledge graph, not just another node.

Division of labour (mirrors the rest of the codebase: model judges, script
mechanizes):
  - The **model** (the `/wiki-crystallize` skill, main session) reads the
    synthesis narrative, lifts atomic standalone facts, and routes each to the
    best existing home page via the established discovery machinery (grep +
    hybrid_search). That judgment is not scriptable.
  - **This module** (no LLM) takes the model's `[{fact, target}]` list and
    mechanizes the provenance-bearing parts: trace-determined tag + `sources:`,
    a dedup check against the target page, a unique `{#claim-id}` anchor, and a
    ready-to-merge claim line. It emits a structured plan the skill surfaces at
    the HITL fan-out gate before any write.

Provenance is trace-determined (the payoff of the A1/A2/A3 claim bundle):
  - VERBATIM trace hit ⇒ the fact is directly attested in a specific source ⇒
    **EXTRACTED** (untagged — the implicit default per the claim-tag schema),
    `sources:` pinned to that source (∩ the synthesis page's own `sources:`, the
    session-107 S5 guard against a coincidental hit in an uncited file).
  - No verbatim home ⇒ the fact is synthesized across sources ⇒ **`*[INFERRED]*`**,
    `sources:` = the synthesis page's underlying sources (its `sources:` union).
    Build-time refinement of the design's "point at the synthesis page": pointing
    `sources:` at the underlying vault files keeps the frontmatter schema-consistent
    (every other page's `sources:` are vault paths) and keeps the A3 cascade able
    to reach the fact through its real evidence chain.

Every merged fact gets a `{#claim-id}` anchor so the A3 `cascade_review` lint
tracks it from day one.

Routing is **merge-only** (v1): a fact with no existing home is reported as
`unhomed` for the user to decide later — v1 never auto-creates pages.

Library first: `build_plan` is the API the skill calls. A minimal `main()` is
provided for live verification / debugging only; it is not a user-facing surface.

Usage (debug):
    python crystallize.py <wiki-root> <synthesis-slug> --facts-json FILE [--vault DIR] [--json]
    # FILE (or - for stdin) is a JSON list of {"fact": "...", "target": "slug-or-null"}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import trace_claim

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# `{#claim-<slug>}` anchor (A2). Mirrors lint_wiki.CLAIM_ID_RE / cascade_claim's
# copy — kept local so the module has no lint_wiki import dependency.
CLAIM_ID_RE = re.compile(r"\{#(claim-[a-z0-9-]+)\}")
_WORD = re.compile(r"[a-z0-9]+")
# Function words dropped when minting a claim-id slug, so `claim-<slug>` keys off
# the fact's content, not its connective tissue. Small by design — a crude filter,
# not a maintained stoplist (same philosophy as trace_claim.content_words).
_SLUG_STOP = frozenset(
    "a an the is are was were be been being of to in on at by for with from "
    "and or but as that this these those it its their his her they them we our "
    "has have had do does did can will would should could may might must not "
    "into over under than then so such which who whom whose what when where".split()
)
_MAX_SLUG_TOKENS = 5
_INFERRED_TAG = "*[INFERRED]*"


def source_slugs(frontmatter: dict) -> set[str]:
    """Slugs (filename stems) of a page's `sources:` frontmatter entries.

    `sources:` carries project-root paths (`vault/Web Articles/<slug>.md`); the
    stem is the stable identity that matches `trace`'s `slug` field. (Same helper
    as `cascade_claim.source_slugs`, re-derived here to avoid the cross-import.)
    """
    srcs = frontmatter.get("sources") or []
    if not isinstance(srcs, list):
        return set()
    return {Path(s).stem for s in srcs if isinstance(s, str) and s.strip()}


def source_paths(frontmatter: dict) -> list[str]:
    """The raw `sources:` path strings, order-preserved, blanks dropped."""
    srcs = frontmatter.get("sources") or []
    if not isinstance(srcs, list):
        return []
    return [s for s in srcs if isinstance(s, str) and s.strip()]


def slugify_claim(fact: str, taken: set[str]) -> str:
    """Mint a unique `claim-<slug>` from a fact's content words.

    Takes the first few content tokens (stopwords + sub-3-char words dropped),
    joins them, prefixes `claim-`, and disambiguates against `taken` by appending
    `-2`, `-3`, … so per-page uniqueness (the `claim_id_issues` lint contract) holds.
    """
    toks = [w for w in _WORD.findall(fact.lower()) if len(w) >= 3 and w not in _SLUG_STOP]
    stem = "-".join(toks[:_MAX_SLUG_TOKENS]) or "fact"
    base = f"claim-{stem}"
    cid = base
    n = 2
    while cid in taken:
        cid = f"{base}-{n}"
        n += 1
    return cid


def is_present(fact: str, target_body: str) -> bool:
    """True if `fact` already appears on the target page (normalized substring).

    Reuses `trace_claim.normalize` (lowercase, strip md noise, fold number-words,
    collapse whitespace) symmetrically, so a cosmetically-reformatted restatement
    of the fact is still caught and not merged as a duplicate.
    """
    return trace_claim.normalize(fact) in trace_claim.normalize(target_body)


def attribute_fact(
    fact: str, vault_root: Path | None, synthesis_fm: dict
) -> dict:
    """Trace-determine a fact's tag + `sources:`.

    Returns `{tag, sources, provenance}` where `tag` is "" (EXTRACTED, implicit)
    or "*[INFERRED]*", `sources` is a list of vault path strings, and `provenance`
    is one of `verbatim` / `synthesized`.
    """
    syn_slugs = source_slugs(synthesis_fm)
    if vault_root is not None and vault_root.is_dir() and fact.strip():
        res = trace_claim.trace(vault_root, fact)
        if res["match_type"] == "verbatim":
            # Pin to the matched source(s), intersected with the synthesis page's
            # own sources so a coincidental verbatim hit in an uncited vault file
            # is not adopted as provenance (the S5 guard).
            matched = {(r["slug"], r["file"]) for r in res["results"]}
            pinned = [(s, f) for (s, f) in matched if s in syn_slugs] or list(matched)
            paths = sorted({f"vault/{f}" for (_s, f) in pinned})
            return {"tag": "", "sources": paths, "provenance": "verbatim"}
    # No verbatim home: synthesized across the synthesis page's evidence base.
    return {
        "tag": _INFERRED_TAG,
        "sources": source_paths(synthesis_fm),
        "provenance": "synthesized",
    }


def make_claim_line(fact: str, tag: str, claim_id: str) -> str:
    """Format a merge-ready bullet: `- <fact> [*[INFERRED]*] {#claim-id}`.

    Tag (when present) sits after the fact and before the anchor, separated by
    single spaces — the order the claim-tag schema and `trace_claim` expect.
    """
    fact = fact.strip()
    pieces = ["- ", fact]
    if tag:
        pieces.append(f" {tag}")
    pieces.append(f" {{#{claim_id}}}")
    return "".join(pieces)


def build_plan(
    facts: list[dict], pages: dict, vault_root: Path | None, synthesis_slug: str
) -> dict:
    """Mechanize a crystallization merge plan from the model's extracted facts.

    `facts` is `[{"fact": str, "target": slug-or-None}, …]` (the model's lift +
    route). Returns a plan dict whose `rows` carry per-fact merge instructions and
    a `summary` of status counts, ready for the skill's HITL fan-out gate.

    Status per row:
      - `merge`           — new fact with a home; carries tag, sources, claim_id, claim_line.
      - `already_present` — the home page already states it (dedup); skipped.
      - `unhomed`         — no existing home (merge-only ⇒ reported, never created).
    """
    if synthesis_slug not in pages:
        raise KeyError(f"synthesis page not found in wiki: {synthesis_slug}")
    syn_info = pages[synthesis_slug]
    syn_fm = syn_info["frontmatter"]

    # Per-page claim-id ledger: seed from each page's existing anchors so minted
    # IDs never collide with what's already on the page, and accumulate across this
    # run so two facts routed to the same page don't collide with each other.
    taken: dict[str, set[str]] = {}

    rows: list[dict] = []
    for entry in facts:
        fact = (entry.get("fact") or "").strip()
        target = entry.get("target") or None
        row: dict = {"fact": fact, "target_slug": target}

        if not fact:
            row["status"] = "unhomed"
            row["note"] = "empty fact text"
            rows.append(row)
            continue
        if not target or target not in pages:
            row["status"] = "unhomed"
            rows.append(row)
            continue

        target_info = pages[target]
        row["target_path"] = target_info["path"]
        if is_present(fact, target_info["body"]):
            row["status"] = "already_present"
            rows.append(row)
            continue

        page_taken = taken.setdefault(
            target, set(CLAIM_ID_RE.findall(target_info["body"]))
        )
        attr = attribute_fact(fact, vault_root, syn_fm)
        cid = slugify_claim(fact, page_taken)
        page_taken.add(cid)

        existing_src = set(source_paths(target_info["frontmatter"]))
        row.update(
            {
                "status": "merge",
                "tag": attr["tag"],
                "provenance": attr["provenance"],
                "sources": attr["sources"],
                "sources_to_add": [s for s in attr["sources"] if s not in existing_src],
                "claim_id": cid,
                "claim_line": make_claim_line(fact, attr["tag"], cid),
            }
        )
        rows.append(row)

    summary = {
        "merge": sum(1 for r in rows if r["status"] == "merge"),
        "already_present": sum(1 for r in rows if r["status"] == "already_present"),
        "unhomed": sum(1 for r in rows if r["status"] == "unhomed"),
        "total": len(rows),
    }
    return {
        "synthesis_slug": synthesis_slug,
        "synthesis_path": syn_info["path"],
        "rows": rows,
        "summary": summary,
    }


# ---------- debug CLI (not a user-facing surface) ----------
def _read_facts(spec: str) -> list[dict]:
    raw = sys.stdin.read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("facts JSON must be a list of {fact, target} objects")
    return data


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Crystallization merge planner (debug).")
    parser.add_argument("wiki_root", type=Path)
    parser.add_argument("synthesis_slug")
    parser.add_argument("--facts-json", required=True, help="JSON file of [{fact,target}], or - for stdin")
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    wiki_root = args.wiki_root.resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} is not a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2
    vault_root = args.vault.resolve() if args.vault else (wiki_root.parent / "vault")
    if not vault_root.is_dir():
        vault_root = None

    try:
        facts = _read_facts(args.facts_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read facts JSON: {exc}", file=sys.stderr)
        return 2

    import lint_wiki  # lazy — only the debug CLI needs collect_pages

    pages = lint_wiki.collect_pages(wiki_root)
    try:
        plan = build_plan(facts, pages, vault_root, args.synthesis_slug)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(plan, indent=2))
        return 0

    s = plan["summary"]
    print(f"Crystallize {plan['synthesis_path']} → {s['merge']} merge / "
          f"{s['already_present']} already-present / {s['unhomed']} unhomed")
    print()
    for r in plan["rows"]:
        if r["status"] == "merge":
            print(f"  [merge → {r['target_slug']}] ({r['provenance']})")
            print(f"    {r['claim_line']}")
            if r["sources_to_add"]:
                print(f"    +sources: {', '.join(r['sources_to_add'])}")
        elif r["status"] == "already_present":
            print(f"  [already on {r['target_slug']}] {r['fact'][:70]}")
        else:
            print(f"  [unhomed] {r['fact'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
