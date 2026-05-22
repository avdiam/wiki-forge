#!/usr/bin/env python3
"""Enumerate typed `contradicts:` edges as unique symmetric pairs.

Read-only scan. Glob wiki/pages/{concepts,entities,comparisons,syntheses,sources}/*.md;
parse `contradicts:` frontmatter; dedupe symmetric pairs into unique (A, B)
tuples sorted by slug; emit JSON on stdout sorted by oldest `last_updated`
of either side (least-recently-touched first — same proxy
list_verify_flags.py uses for "oldest queue item first").

The `contradicts:` field is schema-symmetric: A listing B implies B lists A
(enforced by lint_wiki.py's check_asymmetric_contradictions). Iterating by
unique pair instead of by directional edge halves the work and makes the
three-outcome decision menu in /wiki-resolve-contradictions one decision
per pair instead of two.

Output shape:

    {
      "total": 9,
      "returned": 9,
      "by_shape": {"source-source": 4, "source-concept": 2,
                   "source-entity": 1, "concept-concept": 1,
                   "concept-comparison": 1},
      "pairs": [
        {
          "pair_key": "agents-md|2026-02-12-evaluating-agents-md-...",
          "pair_a_slug": "agents-md",
          "pair_a_path": "wiki/pages/concepts/agents-md.md",
          "pair_a_type": "concept",
          "pair_a_category": "tool-use-and-skills",
          "pair_a_last_updated": "2026-05-09",
          "pair_b_slug": "2026-02-12-evaluating-agents-md-...",
          "pair_b_path": "wiki/pages/sources/2026-02-12-...md",
          "pair_b_type": "source",
          "pair_b_category": "tool-use-and-skills",
          "pair_b_last_updated": "2026-02-12",
          "shape": "concept-source",
          "asymmetric": false,
          "callout_excerpt": "...prior framing — context files as helpful..."
        },
        ...
      ]
    }

Pair-key format: `<slug-a>|<slug-b>` with slugs sorted ascending. Stable
across runs; `--single` accepts the key in either slug order.

`asymmetric: true` means the edge is one-directional (A lists B but not
vice-versa, or vice-versa). lint_wiki.py is the authoritative surface for
that condition; this helper still emits the pair so the caller can see
the queue. `check_invalid_typed_links` violations (target slug doesn't
resolve) are silently skipped — they're a lint problem, not a resolution
candidate.

`callout_excerpt` is the body's `> [!WARNING] Contradiction Detected:`
callout text (first ~280 chars, line-wraps collapsed) from whichever side
carries it. Source pages typically don't carry the callout — the prose
anchor lives on the aggregating concept/entity page. `null` when neither
side has a callout (true for source↔source pairs where the contradiction
is structural-only).

Used by `/wiki-resolve-contradictions` to drive its per-pair WebSearch
loop.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from frontmatter import parse_frontmatter  # noqa: E402

SCOPED_DIRS = ("concepts", "entities", "comparisons", "syntheses", "sources")

# Callout span: ` > [!WARNING] ... Contradiction Detected: <body>` — body may
# be on the same line (single-line callout) or continue across subsequent
# blockquote `> ...` lines (multi-line callout). Capture group is the body
# starting after `Detected:` (or `Contradiction:` variant), absorbing
# `>`-prefixed continuation lines until a non-`>` line or blank line.
_CALLOUT_RE = re.compile(
    r"\[!WARNING\][^\n]*?Contradiction[^:\n]*:\s*"
    r"([^\n]*(?:\n>[^\n]*)*)",
    re.MULTILINE,
)

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")


def _slug_from_wikilink(raw: str) -> str:
    """Strip `[[…]]` (with optional pipe alias) to bare slug; pass through
    bare slugs unchanged."""
    if raw is None:
        return ""
    s = str(raw).strip()
    m = _WIKILINK_RE.search(s)
    return m.group(1).strip() if m else s


def _extract_callout(body: str) -> str | None:
    m = _CALLOUT_RE.search(body)
    if not m:
        return None
    raw = m.group(1)
    # Strip leading `> ` from continuation lines; collapse whitespace runs.
    text = re.sub(r"\n>\s?", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if len(text) > 280:
        text = text[:277] + "..."
    return text


def _split_body(text: str) -> str:
    """Return body portion (after closing --- of frontmatter)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :]
    return text


def _read_pages(wiki_root: Path) -> dict[str, dict]:
    """Index pages by slug with frontmatter + body callout extracted."""
    pages: dict[str, dict] = {}
    for sub in SCOPED_DIRS:
        d = wiki_root / "pages" / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            body = _split_body(text)
            pages[p.stem] = {
                "path": p.relative_to(wiki_root.parent).as_posix(),
                "type": fm.get("type") or sub.rstrip("s"),
                "category": fm.get("category"),
                "last_updated": fm.get("last_updated"),
                "contradicts": fm.get("contradicts") or [],
                "callout": _extract_callout(body),
            }
    return pages


def _collect_pairs(pages: dict[str, dict]) -> list[dict]:
    """Walk every page; for each `contradicts:` entry build a sorted-pair
    key; dedupe; mark asymmetric edges."""
    pair_data: dict[tuple[str, str], dict] = {}
    for slug, info in pages.items():
        targets = info["contradicts"]
        if not isinstance(targets, list):
            continue
        for raw in targets:
            tgt = _slug_from_wikilink(raw)
            if not tgt or tgt not in pages:
                # Silent skip — invalid typed link is a lint concern, not
                # a resolution candidate. lint_wiki's
                # check_invalid_typed_links is the authoritative surface.
                continue
            a, b = sorted([slug, tgt])
            key = (a, b)
            if key in pair_data:
                # Symmetric back-edge — confirmed, mark not asymmetric.
                pair_data[key]["seen_both"] = (
                    pair_data[key]["seen_both"] or slug != pair_data[key]["first_seen_from"]
                )
                continue
            pair_data[key] = {"first_seen_from": slug, "seen_both": False}

    # Second pass: for each pair, verify symmetry by checking back-edge.
    for (a, b), state in pair_data.items():
        a_to_b = any(_slug_from_wikilink(t) == b for t in pages[a]["contradicts"])
        b_to_a = any(_slug_from_wikilink(t) == a for t in pages[b]["contradicts"])
        state["asymmetric"] = not (a_to_b and b_to_a)

    pairs: list[dict] = []
    for (a, b), state in pair_data.items():
        info_a, info_b = pages[a], pages[b]
        # Callout from whichever side carries it (prefer the
        # aggregator — concept/entity over source; comparison over concept).
        callout = info_a["callout"] or info_b["callout"]
        type_a, type_b = info_a["type"], info_b["type"]
        shape = "-".join(sorted([type_a, type_b]))
        pairs.append(
            {
                "pair_key": f"{a}|{b}",
                "pair_a_slug": a,
                "pair_a_path": info_a["path"],
                "pair_a_type": type_a,
                "pair_a_category": info_a["category"],
                "pair_a_last_updated": info_a["last_updated"],
                "pair_b_slug": b,
                "pair_b_path": info_b["path"],
                "pair_b_type": type_b,
                "pair_b_category": info_b["category"],
                "pair_b_last_updated": info_b["last_updated"],
                "shape": shape,
                "asymmetric": state["asymmetric"],
                "callout_excerpt": callout,
            }
        )

    def _oldest(row: dict) -> str:
        return min(
            row["pair_a_last_updated"] or "9999-99-99",
            row["pair_b_last_updated"] or "9999-99-99",
        )

    pairs.sort(key=lambda r: (_oldest(r), r["pair_key"]))
    return pairs


def _apply_filters(pairs: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.single:
        target = args.single.strip()
        # Accept either order — normalize by sorting slugs.
        parts = re.split(r"[|:~]", target)
        if len(parts) == 2:
            a, b = sorted([parts[0].strip(), parts[1].strip()])
            key = f"{a}|{b}"
        else:
            key = target  # Already a pair_key
        return [p for p in pairs if p["pair_key"] == key]
    if args.batch:
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", args.batch)
        if not m:
            raise SystemExit(f"error: --batch must be 'X-Y' (got: {args.batch!r})")
        start, end = int(m.group(1)), int(m.group(2))
        if start < 1 or end < start:
            raise SystemExit(f"error: --batch range invalid (got: {args.batch!r})")
        return pairs[start - 1 : end]
    if args.all:
        return pairs
    return pairs[:10]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--wiki", default="wiki", help="Path to the wiki root (default: wiki)")
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--single",
        help="Single pair: 'slug-a|slug-b' (either order). Colon or tilde also accepted.",
    )
    g.add_argument("--batch", help="Range 'X-Y' (1-indexed inclusive)")
    g.add_argument("--all", action="store_true", help="Emit all pairs")
    p.add_argument(
        "--total-only",
        action="store_true",
        help="Print just the total pair count to stdout (for cheap counting)",
    )
    args = p.parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(
            f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)",
            file=sys.stderr,
        )
        return 2

    pages = _read_pages(wiki_root)
    pairs = _collect_pairs(pages)
    if args.total_only:
        print(len(pairs))
        return 0

    selected = _apply_filters(pairs, args)
    by_shape: dict[str, int] = {}
    for r in pairs:
        by_shape[r["shape"]] = by_shape.get(r["shape"], 0) + 1
    out = {
        "total": len(pairs),
        "returned": len(selected),
        "by_shape": by_shape,
        "pairs": selected,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
