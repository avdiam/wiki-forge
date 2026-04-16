#!/usr/bin/env python3
"""Pre-process a source file for LLM ingestion.

This is a helper — it does NOT call an LLM. It extracts metadata from a
source file in vault/ and emits a JSON brief that the wiki-ingestor
subagent can consume. This saves the LLM from mechanical work (word counts,
title extraction, duplicate detection) and catches re-ingests early.

Supported source types (stdlib only):
  .md  .txt  .html  .htm  .json  .csv

For .pdf and binary formats, the LLM reads the file directly via its
Read tool — this script just provides the metadata envelope.

Usage:
    python ingest_source.py --wiki <wiki-root> --source <raw-file-path>
    python ingest_source.py --wiki <wiki-root> --source <raw-file-path> --json

Output (JSON):
    {
      "source_path": "vault/2026-04-15-article.md",
      "title_guess": "Article Title",
      "word_count": 8432,
      "sha256_short": "a1b2c3d4e5f6",
      "ext": ".md",
      "preview": "First 1200 chars...",
      "existing_summary": "sources/2026-04-15-article.md" | null,
      "merge_mode": true | false,
      "suggested_slug": "2026-04-15-article"
    }
"""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import re
import sys
from pathlib import Path

PREVIEW_CHARS = 1200
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = SLUG_RE.sub("-", text).strip("-")
    return text[:60] or "untitled"


# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------

class _HTMLTextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title: str | None = None
        self._in_title = False
        self._skip = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self._skip = True
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title and self.title is None:
            self.title = data.strip() or None
        else:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def extract_text(path: Path) -> tuple[str, str | None]:
    """Extract text and optional title from a source file.

    Returns (text, title_or_none).
    """
    ext = path.suffix.lower()
    data = path.read_bytes()

    if ext in {".md", ".txt"}:
        text = data.decode("utf-8", errors="replace")
        title = None
        # Check YAML frontmatter for title
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                for line in text[3:end].splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        if k.strip() == "title":
                            title = v.strip().strip('"').strip("'")
                            break
        # Fallback: first H1
        if not title:
            for line in text.splitlines()[:30]:
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        return text, title

    if ext in {".html", ".htm"}:
        parser = _HTMLTextExtractor()
        try:
            parser.feed(data.decode("utf-8", errors="replace"))
        except Exception:
            pass
        return parser.text(), parser.title

    if ext == ".json":
        try:
            obj = json.loads(data.decode("utf-8", errors="replace"))
            return json.dumps(obj, indent=2)[:100000], None
        except Exception:
            return data.decode("utf-8", errors="replace"), None

    if ext == ".csv":
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[:50]), None

    # Unknown: attempt utf-8 decode
    try:
        return data.decode("utf-8", errors="replace"), None
    except Exception:
        return "", None


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build_brief(wiki_root: Path, source_path: Path) -> dict:
    """Build a JSON brief for the ingestor agent."""
    try:
        rel = source_path.relative_to(wiki_root.parent)
    except ValueError:
        rel = source_path

    text, title = extract_text(source_path)
    title_guess = title or source_path.stem.replace("-", " ").replace("_", " ").title()

    # Check for existing summary page (→ merge mode)
    # Sources live directly under wiki root: sources/<slug>.md
    slug = source_path.stem
    summary_path = wiki_root / "sources" / f"{slug}.md"
    existing = str(summary_path.relative_to(wiki_root)).replace("\\", "/") if summary_path.is_file() else None

    return {
        "source_path": str(rel).replace("\\", "/"),
        "title_guess": title_guess,
        "word_count": len(text.split()),
        "sha256_short": hashlib.sha256(source_path.read_bytes()).hexdigest()[:16],
        "ext": source_path.suffix.lower(),
        "preview": text[:PREVIEW_CHARS],
        "existing_summary": existing.replace("\\", "/") if existing else None,
        "merge_mode": existing is not None,
        "suggested_slug": slug,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-process a source file for LLM ingestion"
    )
    parser.add_argument("--wiki", required=True, help="Path to the wiki root")
    parser.add_argument("--source", required=True, help="Path to the raw source file")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args()

    wiki_root = Path(args.wiki).resolve()
    source = Path(args.source).resolve()

    if not source.is_file():
        print(f"error: source not found: {source}", file=sys.stderr)
        return 2

    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    brief = build_brief(wiki_root, source)

    if args.json:
        print(json.dumps(brief, indent=2, ensure_ascii=False))
    else:
        print(f"Source:          {brief['source_path']}")
        print(f"Title (guess):   {brief['title_guess']}")
        print(f"Size:            {brief['word_count']} words")
        print(f"SHA256 (short):  {brief['sha256_short']}")
        print(f"Type:            {brief['ext']}")
        print(f"Suggested slug:  {brief['suggested_slug']}")
        if brief["merge_mode"]:
            print(f"EXISTING PAGE:   {brief['existing_summary']} — MERGE MODE")
        else:
            print(f"Summary page:    (will create sources/{brief['suggested_slug']}.md)")
        print()
        print("--- preview ---")
        print(brief["preview"])
        print("--- /preview ---")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
