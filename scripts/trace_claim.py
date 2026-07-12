#!/usr/bin/env python3
"""Quote-level provenance: trace a wiki claim back to its raw-vault source span.

`/wiki-trace` slice A1 (claim-provenance bundle, Phase 2). Given a phrase or
claim lifted from a compiled wiki page, find the raw-vault passage it came from
and return the surrounding paragraph + filename + line. Closes the Mode 3
(truth compression) gap: a reader doubting one assertion can locate its exact
source without re-reading the whole vault file.

Two-tier match, paragraph granularity:
  1. VERBATIM — the query, whitespace-normalized + lowercased, is a substring of
     a normalized vault paragraph. This is true provenance: the claim was
     EXTRACTED directly from that span.
  2. FUZZY (fallback, only when no verbatim hit) — lightweight stdlib
     token-containment: fraction of the query's content words (len >= 3) present
     in a paragraph. Surfaces the closest paraphrase candidates for INFERRED /
     AMBIGUOUS claims that have no verbatim home. NOT exact provenance — labeled
     as such. Candidates are score-tiered (trace v1.2): a high-containment
     outlier (score >= --strong-score, default 0.75) is a LIKELY VERBATIM HOME —
     the claim was probably EXTRACTED and the compiler reformatted it past what
     normalize() folds — while flat scores near the 0.5 floor are TOPICAL NOISE,
     consistent with a genuinely synthesized claim. (Semantic fallback over a
     vault-side embedding index is a deferred A2+ enhancement; hybrid_search.py
     indexes the wiki, not the vault.)

Searches `vault/`, excluding any file whose first vault-relative path segment
starts with `.` or `_` (the canonical ingest-exclusion rule — auto-skips
`.skipped/`, `_mailbox/`, `.archive/`, and protocol dotfiles). Pure stdlib;
the vault is small enough that a full walk is sub-second, so no index is built.

Usage:
    python trace_claim.py <vault-root> "<quote or claim>" [--max-results N]
                          [--min-score F] [--json]

Exit codes:
    0  ok (results printed; "no match" is still a 0 — a clean negative answer)
    2  invalid args / empty query / vault root not found
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+")

# Inline-markdown noise the wiki compiler adds when rendering a directly-EXTRACTED
# claim — backtick code spans and **bold** labels — plus the `→` flow arrow it
# inserts in pipelines. Stripped symmetrically on the query and every vault
# paragraph so a cosmetically-reformatted claim still resolves verbatim instead of
# dropping to the fuzzy tier. A lone `*` (glob, emphasis, multiplication) is
# deliberately NOT stripped — only the `**` bold pair — to avoid degenerate
# matches like `*.md` becoming a substring of every `.md` path in the vault.
_MD_NOISE = re.compile(r"`|\*\*")
_ARROW = re.compile(r"\s*→\s*")
# Small number-words 0–19 + tens 20–90 → canonical digit form, so a claim the
# compiler tabularized as "3" still matches a source that wrote "three". Bounded
# and word-boundary-anchored (no compositional hundreds/thousands; `one` inside
# `someone`, `ten` inside `often` are left alone) to keep the fold conservative.
_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}
_NUM_RE = re.compile(r"\b(" + "|".join(_NUM_WORDS) + r")\b")

# Fuzzy score-tier boundary (trace v1.2). Without it the fuzzy tier is "two
# tools under one label": a high-containment outlier signals a *mangled verbatim
# home* (the claim IS extracted; the compiler reformatted it past what
# normalize() folds), while flat scores near the 0.5 floor are topical
# vocabulary overlap — consistent with a genuinely synthesized (INFERRED /
# AMBIGUOUS) claim. The boundary is absolute, not top-vs-rest gap-based: the
# vault holds near-duplicate clips of the same article, and a gap rule would
# mislabel twin verbatim homes as noise precisely when the evidence is
# strongest. Empirical anchors (session-107 trace exercise + the live
# kaitai-dong claim): mangled verbatim homes scored 1.0 and 0.778; synthesized
# claims produced flat 0.5.
STRONG_SCORE_DEFAULT = 0.75

# A2 (claim-ID anchors). `[[page-slug#claim-<slug>]]` query form → resolve the
# claim text on the wiki page, then trace that text into the vault. The body
# anchor is `{#claim-<slug>}` placed after a claim's terminal punctuation.
ANCHOR_QUERY_RE = re.compile(r"^\[\[\s*([^\]|#]+?)\s*#\s*(claim-[a-z0-9-]+)\s*\]\]$")
# Strip trailing claim-tag markers (*[INFERRED]* / *[AMBIGUOUS]*) that sit
# between the claim's terminal punctuation and the anchor.
_TAG_TAIL_RE = re.compile(r"(?:\s*\*\[(?:INFERRED|AMBIGUOUS)\]\*)+\s*$")
_LEAD_BLOCKQUOTE_RE = re.compile(r"^(?:\s*>+\s*)+")
_LEAD_BULLET_RE = re.compile(r"^(?:[-*+]\s+|\d+\.\s+)")


def normalize(text: str) -> str:
    """Lowercase, strip inline-markdown noise, fold small number-words to digits,
    and collapse whitespace runs to single spaces, stripped.

    Beyond the original lowercase+whitespace pass, this strips backticks / **bold**
    / `→` arrows and folds number-words (`three` → `3`). Run symmetrically on the
    query and every vault paragraph, it lets a directly-EXTRACTED claim survive the
    cosmetic reformatting the wiki compiler applies (code-span backticks, bolded
    labels, digit-vs-word tables) and still resolve to its verbatim source span —
    closing the reformat-miss class the session-107 trace-exercise surfaced (S2/S4a).
    """
    t = _MD_NOISE.sub("", text.lower())
    t = _ARROW.sub(" ", t)
    t = _NUM_RE.sub(lambda m: _NUM_WORDS[m.group(0)], t)
    return _WS.sub(" ", t).strip()


def content_words(text: str) -> set[str]:
    """Content-word token set: lowercase alphanumeric runs of length >= 3.

    The length floor is a crude stopword filter — drops `a`/`an`/`to`/`of`
    without a maintained stoplist, which is enough to keep the fuzzy
    containment score from being dominated by function words.
    """
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 3}


def iter_paragraphs(text: str):
    """Yield (start_line, paragraph_text) for blank-line-delimited paragraphs.

    `start_line` is 1-based and points at the paragraph's first non-blank line
    in the original file, so the caller can report a locatable position.
    """
    lines = text.splitlines()
    buf: list[str] = []
    start = 0
    for i, line in enumerate(lines, 1):
        if line.strip() == "":
            if buf:
                yield start, "\n".join(buf)
                buf = []
        else:
            if not buf:
                start = i
            buf.append(line)
    if buf:
        yield start, "\n".join(buf)


def is_excluded(rel: Path) -> bool:
    """True if the vault-relative path's first segment starts with `.` or `_`."""
    first = rel.parts[0] if rel.parts else ""
    return first.startswith(".") or first.startswith("_")


def iter_vault_files(vault_root: Path):
    """Yield (relative_path, absolute_path) for traceable vault markdown files."""
    for path in sorted(vault_root.rglob("*.md")):
        rel = path.relative_to(vault_root)
        if is_excluded(rel):
            continue
        yield rel, path


def _window(paragraph: str, needle_norm: str, max_chars: int) -> str:
    """Trim an over-long paragraph to a readable window around the match.

    For verbatim hits, centers the window on the matched span; for everything
    else, returns a head window. Adds ellipses where content is dropped.
    """
    if len(paragraph) <= max_chars:
        return paragraph
    lowered = paragraph.lower()
    idx = lowered.find(needle_norm.split(" ")[0]) if needle_norm else -1
    if idx < 0:
        return paragraph[:max_chars].rstrip() + " …"
    half = max_chars // 2
    start = max(0, idx - half)
    end = min(len(paragraph), start + max_chars)
    start = max(0, end - max_chars)
    snippet = paragraph[start:end].strip()
    if start > 0:
        snippet = "… " + snippet
    if end < len(paragraph):
        snippet = snippet + " …"
    return snippet


def extract_claim_sentence(before_anchor: str) -> str:
    """Given the text on a line up to (not including) the `{#claim-id}` anchor,
    return the claim sentence.

    The anchor sits after a claim's terminal punctuation, after any `*[TAG]*`
    marker. So: strip the trailing tag marker(s), take the last sentence (the
    anchor terminates one claim, not necessarily the whole line), then strip
    leading blockquote / list markdown noise.
    """
    s = _TAG_TAIL_RE.sub("", before_anchor.rstrip()).rstrip()
    sentences = re.split(r"(?<=[.!?])\s+", s)
    claim = sentences[-1].strip() if sentences else s
    claim = _LEAD_BLOCKQUOTE_RE.sub("", claim)
    claim = _LEAD_BULLET_RE.sub("", claim)
    return claim.strip()


def resolve_claim_anchor(wiki_root: Path, page_slug: str, claim_id: str) -> dict:
    """Resolve a `[[page-slug#claim-id]]` anchor to the claim text on the page.

    Globs `wiki_root/pages/**/<page-slug>.md`, finds the line bearing the
    `{#claim-id}` anchor, and extracts the claim sentence ending at it.

    Returns `{ok: True, page, line, claim_text}` or `{ok: False, error}`.
    """
    matches = sorted(wiki_root.glob(f"pages/**/{page_slug}.md"))
    if not matches:
        return {
            "ok": False,
            "error": f"page not found: no pages/**/{page_slug}.md under {wiki_root}",
        }
    page = matches[0]
    try:
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": f"cannot read {page}: {exc}"}
    rel = page.relative_to(wiki_root).as_posix()
    needle = "{#" + claim_id + "}"
    for i, line in enumerate(text.splitlines(), 1):
        idx = line.find(needle)
        if idx >= 0:
            return {
                "ok": True,
                "page": rel,
                "line": i,
                "claim_text": extract_claim_sentence(line[:idx]),
            }
    return {"ok": False, "error": f"anchor {{#{claim_id}}} not found in {rel}"}


def trace(
    vault_root: Path,
    query: str,
    max_results: int = 5,
    min_score: float = 0.5,
    max_chars: int = 600,
    strong_score: float = STRONG_SCORE_DEFAULT,
) -> dict:
    """Resolve a quote/claim to its vault provenance.

    Returns a result dict (also the `--json` payload):
      {query, match_type: verbatim|fuzzy|none, files_searched, results: [...],
       duration_ms}
    where each result is {file, slug, line, paragraph, score?, tier?}. `score`
    and `tier` are present only for fuzzy results: `tier` is
    `likely_verbatim_home` (score >= strong_score — the claim probably IS
    extracted, reformatted past what normalize() folds) or `topical_noise`
    (vocabulary overlap, not provenance). Fuzzy payloads additionally carry
    `strong_score` and a `fuzzy_tiers` count summary over the returned results.
    """
    t0 = time.monotonic()
    q_norm = normalize(query)
    q_words = content_words(query)

    verbatim: list[dict] = []
    fuzzy: list[dict] = []
    files_searched = 0

    for rel, path in iter_vault_files(vault_root):
        files_searched += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_posix = rel.as_posix()
        slug = rel.stem
        for start_line, para in iter_paragraphs(text):
            para_norm = normalize(para)
            if q_norm and q_norm in para_norm:
                verbatim.append(
                    {
                        "file": rel_posix,
                        "slug": slug,
                        "line": start_line,
                        "paragraph": _window(para, q_norm, max_chars),
                    }
                )
            elif not verbatim and q_words:
                # Only bother scoring for the fallback while no verbatim hit
                # has been seen anywhere — verbatim always wins outright.
                p_words = content_words(para)
                if not p_words:
                    continue
                score = len(q_words & p_words) / len(q_words)
                if score >= min_score:
                    fuzzy.append(
                        {
                            "file": rel_posix,
                            "slug": slug,
                            "line": start_line,
                            "score": round(score, 3),
                            "paragraph": _window(para, q_norm, max_chars),
                        }
                    )

    if verbatim:
        match_type = "verbatim"
        results = verbatim[:max_results]
    elif fuzzy:
        match_type = "fuzzy"
        fuzzy.sort(key=lambda r: (-r["score"], r["file"], r["line"]))
        results = fuzzy[:max_results]
        for r in results:
            r["tier"] = (
                "likely_verbatim_home"
                if r["score"] >= strong_score
                else "topical_noise"
            )
    else:
        match_type = "none"
        results = []

    out = {
        "query": query,
        "match_type": match_type,
        "files_searched": files_searched,
        "result_count": len(results),
        "results": results,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }
    if match_type == "fuzzy":
        out["strong_score"] = strong_score
        out["fuzzy_tiers"] = {
            "likely_verbatim_home": sum(
                1 for r in results if r["tier"] == "likely_verbatim_home"
            ),
            "topical_noise": sum(
                1 for r in results if r["tier"] == "topical_noise"
            ),
        }
    return out


def render_human(result: dict) -> str:
    """Human-readable markdown. Vault paths are the locator (not [[wikilinks]] —
    these resolve into the raw vault, not the compiled wiki graph)."""
    q = result["query"]
    mt = result["match_type"]
    lines: list[str] = []
    if result.get("input_kind") == "anchor":
        a = result["anchor"]
        rv = result["resolved"]
        lines.append(
            f'Resolved [[{a["page_slug"]}#{a["claim_id"]}]] → '
            f'`{rv["page"]}` (line {rv["line"]}):'
        )
        lines.append(_indent(f'"{rv["claim_text"]}"'))
        lines.append("")
    if mt == "verbatim":
        lines.append(f'Verbatim source for: "{q}"')
        lines.append("")
        for r in result["results"]:
            lines.append(f"`{r['file']}` (line {r['line']})")
            lines.append(_indent(r["paragraph"]))
            lines.append("")
    elif mt == "fuzzy":
        strong = [r for r in result["results"] if r.get("tier") == "likely_verbatim_home"]
        topical = [r for r in result["results"] if r.get("tier") != "likely_verbatim_home"]
        boundary = result.get("strong_score", STRONG_SCORE_DEFAULT)
        lines.append(f'No verbatim match for: "{q}".')
        if strong:
            lines.append(
                f"Likely reformatted verbatim home(s) (score >= {boundary} — "
                "nearly all of the claim's content words in one paragraph; the "
                "claim may be EXTRACTED, reformatted past what normalize() folds "
                "— read the span to confirm):"
            )
            lines.append("")
            for r in strong:
                lines.append(f"`{r['file']}` (line {r['line']}, score {r['score']})")
                lines.append(_indent(r["paragraph"]))
                lines.append("")
        if topical:
            if strong:
                lines.append(
                    "Topical candidates (vocabulary overlap only — not provenance):"
                )
            else:
                lines.append(
                    f"Topical candidates only — no score outlier >= {boundary} "
                    "(stdlib token-overlap, not provenance; a flat list near the "
                    "score floor is consistent with a genuinely synthesized "
                    "INFERRED/AMBIGUOUS claim — check the page's `sources:` "
                    "frontmatter):"
                )
            lines.append("")
            for r in topical:
                lines.append(f"`{r['file']}` (line {r['line']}, score {r['score']})")
                lines.append(_indent(r["paragraph"]))
                lines.append("")
    else:
        lines.append(f'No source span found for: "{q}".')
        lines.append(
            f"Searched {result['files_searched']} vault files; no verbatim or "
            "close paraphrase. The claim may be INFERRED/AMBIGUOUS (synthesized "
            "across sources, no single verbatim home) — check the page's "
            "`sources:` frontmatter."
        )
    return "\n".join(lines).rstrip() + "\n"


def _indent(block: str) -> str:
    return "\n".join("  " + ln for ln in block.splitlines())


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Trace a wiki claim back to its raw-vault source span."
    )
    p.add_argument("vault_root", type=Path, help="Path to the vault root")
    p.add_argument(
        "query",
        help="The quote/claim to trace, or a [[page-slug#claim-id]] anchor (A2)",
    )
    p.add_argument(
        "--wiki",
        type=Path,
        default=None,
        help="Wiki root, for [[page#claim-id]] anchor resolution "
        "(default: sibling 'wiki' dir of the vault root)",
    )
    p.add_argument(
        "--max-results", type=int, default=5, help="Max spans to return (default 5)"
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=0.5,
        help="Min fuzzy token-containment score to surface a candidate (default 0.5)",
    )
    p.add_argument(
        "--strong-score",
        type=float,
        default=STRONG_SCORE_DEFAULT,
        help="Fuzzy score at or above which a candidate is tiered as a likely "
        f"(reformatted) verbatim home rather than topical noise "
        f"(default {STRONG_SCORE_DEFAULT})",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human format")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    vault_root: Path = args.vault_root.resolve()
    if not vault_root.is_dir():
        print(f"error: vault root not found: {vault_root}", file=sys.stderr)
        return 2
    query = args.query.strip().strip('"').strip("'").strip()
    if not query:
        print("error: empty query", file=sys.stderr)
        return 2
    if args.max_results < 1:
        print("error: --max-results must be >= 1", file=sys.stderr)
        return 2

    anchor = ANCHOR_QUERY_RE.match(query)
    if anchor:
        page_slug, claim_id = anchor.group(1), anchor.group(2)
        wiki_root = args.wiki.resolve() if args.wiki else vault_root.parent / "wiki"
        if not (wiki_root / "CLAUDE.md").is_file():
            print(
                f"error: wiki root not found for anchor resolution: {wiki_root} "
                "(pass --wiki <wiki-root>)",
                file=sys.stderr,
            )
            return 2
        res = resolve_claim_anchor(wiki_root, page_slug, claim_id)
        if not res["ok"]:
            print(f"error: {res['error']}", file=sys.stderr)
            return 2
        result = trace(
            vault_root,
            res["claim_text"],
            max_results=args.max_results,
            min_score=args.min_score,
            strong_score=args.strong_score,
        )
        result["input_kind"] = "anchor"
        result["anchor"] = {"page_slug": page_slug, "claim_id": claim_id}
        result["resolved"] = {
            "page": res["page"],
            "line": res["line"],
            "claim_text": res["claim_text"],
        }
    else:
        result = trace(
            vault_root,
            query,
            max_results=args.max_results,
            min_score=args.min_score,
            strong_score=args.strong_score,
        )
        result["input_kind"] = "quote"

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_human(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
