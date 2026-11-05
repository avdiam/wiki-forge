#!/usr/bin/env python3
"""Pre-screen a vault source file for wiki relevance before ingest.

Checks frontmatter quality labels, source_tags overlap with the wiki's
existing tag vocabulary, and keyword density against domain terms.
Outputs a JSON verdict: pass / warn / auto-skip.

Usage:
    python pre_screen.py --wiki <wiki-root> --source <vault-file> [--json]

Exit codes:
    0 = pass (proceed to takeaway pass)
    1 = warn (present reason to user, proceed if they override)
    2 = auto-skip (recommend skip, proceed only on explicit override)
    3 = error (bad input, missing file, etc.)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Allow importing frontmatter.py from the same directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import parse_frontmatter


def extract_wiki_tags(wiki_root: Path) -> set[str]:
    """Extract all unique tags from wiki index files."""
    tags: set[str] = set()
    indexes_dir = wiki_root / "pages" / "indexes"
    if not indexes_dir.is_dir():
        return tags
    for idx_file in indexes_dir.glob("index-*.md"):
        text = idx_file.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"tags:\s*([^\]]+)", text):
            for tag in match.group(1).split(","):
                tag = tag.strip().rstrip("]").strip()
                if tag:
                    tags.add(tag.lower())
    return tags


def extract_wiki_categories(wiki_root: Path) -> set[str]:
    """Extract category headings from the concepts index."""
    cats: set[str] = set()
    concepts_idx = wiki_root / "pages" / "indexes" / "index-concepts.md"
    if concepts_idx.is_file():
        for line in concepts_idx.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("### "):
                cats.add(line[4:].strip().lower())
    return cats


def tokenize_body(text: str, max_words: int = 500) -> list[str]:
    """Extract lowercase word tokens from the body (after frontmatter)."""
    # Strip frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    return words[:max_words]


def parse_watchlist(vault_root: Path) -> dict[str, dict]:
    """Parse vault/.author-watchlist.md into {slug: {level, reason, since}}."""
    watchlist_path = vault_root / ".author-watchlist.md"
    if not watchlist_path.is_file():
        return {}

    text = watchlist_path.read_text(encoding="utf-8", errors="replace")
    entries: dict[str, dict] = {}
    current_slug: str | None = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("### "):
            current_slug = line[4:].strip().lower()
            entries[current_slug] = {"level": "caution", "reason": "", "since": ""}
        elif current_slug and line.startswith("- **level:**"):
            entries[current_slug]["level"] = line.split("**level:**")[1].strip().lower()
        elif current_slug and line.startswith("- **reason:**"):
            entries[current_slug]["reason"] = line.split("**reason:**")[1].strip()
        elif current_slug and line.startswith("- **since:**"):
            entries[current_slug]["since"] = line.split("**since:**")[1].strip()

    return entries


def check_author_watchlist(author: str | None, vault_root: Path) -> dict | None:
    """Check if the source author is on the watchlist. Returns entry or None."""
    if not author:
        return None
    # Author may be a YAML list (e.g. multiple authors) — coerce to a string.
    if isinstance(author, (list, tuple)):
        author = " ".join(str(a) for a in author)
    watchlist = parse_watchlist(vault_root)
    if not watchlist:
        return None

    # Normalize author to slug form for matching
    author_lower = str(author).lower().strip()
    # Try exact slug match and common variations
    for slug, entry in watchlist.items():
        # Match against slug (e.g., "joe-njenga")
        if slug in author_lower.replace(" ", "-").replace(".", "-"):
            return {"slug": slug, **entry}
        # Match against name parts (e.g., "njenga" in "Joe Njenga")
        slug_parts = slug.split("-")
        author_parts = re.split(r"[\s,.()\-]+", author_lower)
        if len(set(slug_parts) & set(author_parts)) >= max(1, len(slug_parts) - 1):
            return {"slug": slug, **entry}

    return None


def pre_screen(wiki_root: Path, source_path: Path, vault_root: Path | None = None) -> dict:
    """Run all checks and return a verdict dict."""
    if not source_path.is_file():
        return {"verdict": "error", "reason": f"File not found: {source_path}"}

    text = source_path.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(text)

    # Infer vault root if not provided
    if vault_root is None:
        # Walk up from source to find vault root (parent of the content folders)
        vault_root = source_path.parent.parent

    # Gather wiki vocabulary
    wiki_tags = extract_wiki_tags(wiki_root)
    wiki_categories = extract_wiki_categories(wiki_root)

    # -- Check 1: quality label --
    quality = fm.get("quality", "")
    if isinstance(quality, str):
        quality = quality.lower().strip()

    # -- Check 2: tag overlap --
    # Merge `tags:` (topical tags, when the capture carries them — legacy
    # 2026-05-29..2026-11-05 clips were annotated in place) with `source_tags:`
    # (/wiki-capture's URL cascade + older clips). Exclude the legacy `clippings`
    # sentinel — it was a pre_screen-silencer marker, not a relevance signal.
    real_tags = fm.get("source_tags", []) or []
    marker_tags = fm.get("tags", []) or []
    if isinstance(real_tags, str):
        real_tags = [real_tags]
    if isinstance(marker_tags, str):
        marker_tags = [marker_tags]
    combined = list(real_tags) + [t for t in marker_tags if t != "clippings"]
    source_tags = {t.lower().strip() for t in combined if isinstance(t, str)}

    tag_overlap = source_tags & wiki_tags
    tag_overlap_ratio = len(tag_overlap) / max(len(source_tags), 1)

    # -- Check 3: keyword density against wiki tags --
    body_words = tokenize_body(text)
    body_word_set = set(body_words)
    # Match against wiki tags (many are multi-word kebab-case like "agent-harness")
    domain_hits = body_word_set & wiki_tags
    # Also check if body words appear as substrings of wiki tags
    for word in body_words:
        for tag in wiki_tags:
            if word in tag or tag in word:
                domain_hits.add(word)
                break
    domain_score = len(domain_hits) / max(len(body_word_set), 1)

    # -- Check 4: promotional signals --
    promo_signals = [
        r"(?:sign up|subscribe|join)\s+(?:now|today|free)",
        r"limited.time.offer",
        r"discount.code",
        r"use.(?:code|coupon)",
        r"click.(?:here|below).to",
        r"(?:free trial|money.back.guarantee)",
        r"(?:affiliate|referral).link",
        r"\$\d+(?:\.\d+)?(?:/mo|/month|/year|/yr)",
    ]
    promo_count = sum(
        1 for pat in promo_signals
        if re.search(pat, text.lower())
    )

    # -- Verdict logic --
    reasons = []
    verdict = "pass"

    # Auto-skip: zero tag overlap AND low domain score
    if len(tag_overlap) == 0 and domain_score < 0.05:
        verdict = "auto-skip"
        reasons.append(
            f"No source_tags overlap with wiki vocabulary and domain keyword "
            f"density is very low ({domain_score:.1%}). Likely off-domain."
        )

    # Auto-skip: heavy promotional signals
    if promo_count >= 3:
        verdict = "auto-skip"
        reasons.append(
            f"Detected {promo_count} promotional patterns (sign-up CTAs, "
            f"pricing, affiliate links). Likely promotional content."
        )

    # Warn: zero tag overlap but some domain keywords
    if verdict == "pass" and len(tag_overlap) == 0 and domain_score >= 0.05:
        verdict = "warn"
        reasons.append(
            f"No source_tags overlap with wiki vocabulary, but some domain "
            f"keywords found ({domain_score:.1%}). May be tangentially relevant."
        )

    # Warn: quality explicitly marked shallow
    if quality == "shallow":
        if verdict == "pass":
            verdict = "warn"
        reasons.append(
            "Source frontmatter marks this source as 'shallow' quality."
        )

    # Warn: some promotional signals
    if promo_count in (1, 2) and "promotional" not in " ".join(reasons):
        if verdict == "pass":
            verdict = "warn"
        reasons.append(
            f"Detected {promo_count} promotional pattern(s). May contain "
            f"marketing content mixed with substance."
        )

    # Warn: no tags at all in frontmatter
    if len(source_tags) == 0:
        if verdict == "pass":
            verdict = "warn"
        reasons.append(
            "No tags in source frontmatter. Cannot assess topic relevance "
            "from metadata alone."
        )

    # -- Check 5: author watchlist --
    author = fm.get("author", None)
    if isinstance(author, str):
        author = author.strip()
    watchlist_hit = check_author_watchlist(author, vault_root)
    if watchlist_hit:
        level = watchlist_hit["level"]
        reason = watchlist_hit["reason"]
        if level == "untrusted":
            verdict = "warn"  # always warn, even if other checks passed
            reasons.insert(0,
                f"AUTHOR WATCHLIST — untrusted: {watchlist_hit['slug']}. "
                f"Reason: {reason}. Requires explicit user override to ingest."
            )
        elif level == "caution":
            reasons.append(
                f"Author watchlist — caution: {watchlist_hit['slug']}. "
                f"Reason: {reason}. Review claims carefully at takeaway pass."
            )
    else:
        watchlist_hit = None

    result = {
        "verdict": verdict,
        "reasons": reasons if reasons else ["Source passes all pre-screening checks."],
        "quality_label": quality or None,
        "author": author,
        "author_watchlist": watchlist_hit["slug"] if watchlist_hit else None,
        "author_trust": watchlist_hit["level"] if watchlist_hit else "trusted",
        "source_tags": sorted(source_tags),
        "tag_overlap": sorted(tag_overlap),
        "tag_overlap_count": len(tag_overlap),
        "domain_score": round(domain_score, 3),
        "promo_signals": promo_count,
        "body_words_sampled": len(body_words),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Pre-screen a vault file for wiki relevance.")
    parser.add_argument("--wiki", required=True, help="Path to wiki root")
    parser.add_argument("--source", required=True, help="Path to vault source file")
    parser.add_argument("--vault", default=None, help="Path to vault root (auto-inferred if omitted)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    args = parser.parse_args()

    wiki_root = Path(args.wiki).resolve()
    source_path = Path(args.source).resolve()
    vault_root = Path(args.vault).resolve() if args.vault else None

    result = pre_screen(wiki_root, source_path, vault_root)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        v = result["verdict"].upper()
        print(f"Verdict: {v}")
        for r in result["reasons"]:
            print(f"  - {r}")
        print(f"Quality label: {result['quality_label'] or '(none)'}")
        print(f"Source tags: {', '.join(result['source_tags']) or '(none)'}")
        print(f"Tag overlap: {result['tag_overlap_count']} ({', '.join(result['tag_overlap']) or 'none'})")
        print(f"Domain score: {result['domain_score']:.1%}")
        print(f"Promo signals: {result['promo_signals']}")

    # Exit code maps to verdict
    exit_codes = {"pass": 0, "warn": 1, "auto-skip": 2, "error": 3}
    sys.exit(exit_codes.get(result["verdict"], 3))


if __name__ == "__main__":
    main()
