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
     as such. (Semantic fallback over a vault-side embedding index is a deferred
     A2+ enhancement; hybrid_search.py indexes the wiki, not the vault.)

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


def normalize(text: str) -> str:
    """Lowercase + collapse all whitespace runs to single spaces, stripped."""
    return _WS.sub(" ", text).strip().lower()


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


def trace(
    vault_root: Path,
    query: str,
    max_results: int = 5,
    min_score: float = 0.5,
    max_chars: int = 600,
) -> dict:
    """Resolve a quote/claim to its vault provenance.

    Returns a result dict (also the `--json` payload):
      {query, match_type: verbatim|fuzzy|none, files_searched, results: [...],
       duration_ms}
    where each result is {file, slug, line, paragraph, score?}. `score` is
    present only for fuzzy results.
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
    else:
        match_type = "none"
        results = []

    return {
        "query": query,
        "match_type": match_type,
        "files_searched": files_searched,
        "result_count": len(results),
        "results": results,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }


def render_human(result: dict) -> str:
    """Human-readable markdown. Vault paths are the locator (not [[wikilinks]] —
    these resolve into the raw vault, not the compiled wiki graph)."""
    q = result["query"]
    mt = result["match_type"]
    lines: list[str] = []
    if mt == "verbatim":
        lines.append(f'Verbatim source for: "{q}"')
        lines.append("")
        for r in result["results"]:
            lines.append(f"`{r['file']}` (line {r['line']})")
            lines.append(_indent(r["paragraph"]))
            lines.append("")
    elif mt == "fuzzy":
        lines.append(f'No verbatim match for: "{q}".')
        lines.append(
            "Closest paraphrase candidates "
            "(stdlib token-overlap — not exact provenance):"
        )
        lines.append("")
        for r in result["results"]:
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
    p.add_argument("query", help="The quote or claim to trace (quote if it has spaces)")
    p.add_argument(
        "--max-results", type=int, default=5, help="Max spans to return (default 5)"
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=0.5,
        help="Min fuzzy token-containment score to surface a candidate (default 0.5)",
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
    result = trace(
        vault_root,
        query,
        max_results=args.max_results,
        min_score=args.min_score,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_human(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
