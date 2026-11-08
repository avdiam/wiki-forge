#!/usr/bin/env python3
"""Canonical source-backlog matcher — the single source of truth for "which
vault files are still un-ingested."

Three consumers share this module so they can never drift apart again:
  - `lint_wiki.check_source_backlog` — the full list, for the lint report.
  - `drill_state.compute_pending` — the count, for the welcome-report `Backlog:`.
  - the statusline — via `drill_state.py --field pending`.

History: these were three separate implementations and they drifted. The third
(an inline bash `comm -23` in `.claude/statusline.sh`) was filename-only, so it
silently re-grew the phantom-backlog bug — reporting 20 pending when 13 were
real — even after the two Python copies were fixed. Unified 2026-06-04 session 90.

Matching is content-identity-first: a vault file is *handled* if its raw-bytes
`sha256[:16]` matches a source page's `source_hash:` frontmatter, OR (fallback)
its filename stem matches a source/skip slug. The hash arm is what makes the
match robust to the established practice of leaving a vault file with its
non-canonical clipper-dropped name (spaces, caps, curly apostrophes, parens)
while the source page gets a canonical kebab slug — under stem-only matching
every such ingest became a permanent phantom.

The `source_hash` value mirrors what `ingest_source.py` writes:
`sha256(path.read_bytes()).hexdigest()[:16]`. `read_source_hash` reads only the
frontmatter head (not the whole page body) so building the hash set across a few
hundred source pages stays cheap on every wake-up drill and statusline render.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Matches a `source_hash:` line inside a frontmatter block. Applied per-line to
# the head only, so a stray body mention can't match.
SOURCE_HASH_LINE_RE = re.compile(r"^\s*source_hash:\s*[\"']?([0-9a-fA-F]+)")


def is_excluded_vault_path(relative_parts: tuple[str, ...]) -> bool:
    """Ingest-exclusion rule: a vault path whose top-level segment begins with
    `.` or `_` is not a source. Catches protocol/config files
    (`.wiki-identity-bootstrap.md`, `.clipper-rules.md`), protocol folders (`_mailbox/`,
    `.skipped/`), and Obsidian internals (`.obsidian/`).
    """
    if not relative_parts:
        return False
    top = relative_parts[0]
    return top.startswith(".") or top.startswith("_")


def vault_file_hash(path: Path) -> str | None:
    """sha256[:16] of a vault file's raw bytes — matches ingest_source.py's source_hash.
    Returns None on read error so the file falls through to stem matching.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def read_source_hash(path: Path) -> str | None:
    """Extract `source_hash:` from a source page, reading only the leading
    `---`…`---` frontmatter block (not the body) for efficiency.
    """
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            if f.readline().strip() != "---":
                return None  # no opening fence → no frontmatter
            for line in f:
                if line.strip() == "---":
                    break  # closing fence → stop before the body
                m = SOURCE_HASH_LINE_RE.match(line)
                if m:
                    return m.group(1)
    except OSError:
        return None
    return None


def collect_source_hashes(sources_dir: Path) -> set[str]:
    """All `source_hash:` values across `pages/sources/*.md` (head-read each)."""
    if not sources_dir.is_dir():
        return set()
    out: set[str] = set()
    for p in sources_dir.glob("*.md"):
        h = read_source_hash(p)
        if h:
            out.add(h)
    return out


def compute_backlog(
    vault_dir: Path,
    handled_slugs: set[str],
    handled_hashes: set[str],
) -> list[str]:
    """Sorted vault-relative posix paths that are neither stem-matched nor
    hash-matched — i.e. genuinely un-ingested. The canonical matcher.

    Callers supply the handled slug/hash sets so each controls how they're
    sourced: `lint_wiki` pulls them from its already-loaded `pages` dict (no
    extra I/O), `drill_state` builds them from disk via `collect_source_hashes`.
    Only stem-unmatched files are byte-hashed, so the per-file hashing is bounded
    by the (small) unmatched set, not the whole vault.
    """
    if not vault_dir.is_dir():
        return []
    backlog: list[str] = []
    for raw_file in sorted(vault_dir.rglob("*.md")):
        relative = raw_file.relative_to(vault_dir)
        if is_excluded_vault_path(relative.parts):
            continue
        if raw_file.stem in handled_slugs:
            continue
        if handled_hashes and vault_file_hash(raw_file) in handled_hashes:
            continue
        backlog.append(relative.as_posix())
    return backlog
