#!/usr/bin/env python3
"""Normalize vault filenames to YYYY-MM-DD-<kebab-slug>.md.

Walks vault content directories (any top-level dir not prefixed with `.` or
`_`) and renames files whose names don't match the canonical form. Bulk
pre-flight for manually-dropped files (e.g., from the Obsidian Chrome
extension); the wiki-ingestor playbook also runs an equivalent gate at
ingest time, so this script is defense-in-depth, not the only gate.

Rules:
    - Canonical form: ^\\d{4}-\\d{2}-\\d{2}-[a-z0-9-]+\\.md$
    - Date prefix preserved when present at start of stem; otherwise today's date prepended.
    - Slug derived from frontmatter `title:` when available, else from the filename body.
    - Apostrophes, em-dashes, unicode punctuation stripped via NFKD + ASCII drop.
    - Non-alphanumerics collapsed to single dashes; lowercased; trimmed.
    - Tracked files renamed via `git mv`; untracked via plain rename.
    - Slug collisions halt the whole run with a clear error — no silent overwrite.

Usage:
    python wiki/.scripts/normalize_vault.py <vault>
    python wiki/.scripts/normalize_vault.py <vault> --dry-run
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from frontmatter import parse_frontmatter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")
DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})[-_ ]?(.*)$")


def kebab(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def read_title(file: Path) -> str | None:
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm = parse_frontmatter(text)
    title = fm.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def normalized_name(file: Path) -> str:
    stem = file.stem
    m = DATE_PREFIX.match(stem)
    if m:
        date, rest = m.group(1), m.group(2)
        title = read_title(file)
        slug = kebab(title) if title else kebab(rest or stem)
    else:
        date = _dt.date.today().isoformat()
        title = read_title(file)
        slug = kebab(title) if title else kebab(stem)
    if not slug:
        slug = "untitled"
    return f"{date}-{slug}.md"


def find_repo_root(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def is_tracked(repo_root: Path, file: Path) -> bool:
    rel = file.relative_to(repo_root)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel)],
        cwd=repo_root, capture_output=True,
    )
    return result.returncode == 0


def iter_content_files(vault: Path):
    """Yield content `*.md` files under `vault`, supporting two invocation shapes.

    - **Vault root** (`vault/`): iterate each top-level content dir (any child
      directory not prefixed with `.` or `_`) and yield its `*.md` files.
    - **Leaf content dir** (e.g. `vault/Clippings/`): when the passed path itself
      holds loose `*.md` files, yield those directly.

    A leaf dir under the vault root (e.g. `vault/Clippings` — the Web Clipper's
    current landing folder) has no child content dirs, so the root-shaped walk
    finds nothing and the old code reported "Nothing to do" while silently
    skipping every file. Yielding the passed dir's loose `*.md` first fixes that
    false-negative, and also covers a vault root that happens to hold loose
    `*.md`. main() counts what this yields to distinguish "all canonical" from
    "no content files found at all".

    The `.`/`_` exclusion that protects protocol/skip dirs in the child walk
    also applies to the passed path itself: pointing the script at
    `vault/.skipped` (or any `.`/`_`-prefixed dir) yields nothing, so skip
    stubs and mailbox files are never normalized as content.

    The same exclusion applies to **files**: a `.`/`_`-prefixed `*.md` is never
    content. This matters most at the vault root, where the owner:wiki protocol
    files live (`.wiki-identity.md`, `.wiki-identity-bootstrap.md`,
    `.author-watchlist.md`, …) —
    `Path.glob("*.md")` matches dotfiles, so without this filter a vault-root
    run would rename them to `YYYY-MM-DD-wiki-identity.md` etc., breaking the
    SessionStart bootstrap hook and every `@vault/.wiki-identity.md` include.
    (Found s130 via a vault-root `--dry-run`.)
    """
    if not _excluded(vault.name):
        for md in sorted(vault.glob("*.md")):
            if not _excluded(md.name):
                yield md
    for child in sorted(vault.iterdir()):
        if not child.is_dir():
            continue
        if _excluded(child.name):
            continue
        for md in sorted(child.glob("*.md")):
            if not _excluded(md.name):
                yield md


def _excluded(name: str) -> bool:
    """Ingest-exclusion rule: `.`/`_`-prefixed names are protocol/metadata."""
    return name.startswith((".", "_"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize vault filenames.")
    ap.add_argument("vault", type=Path, help="Path to vault directory")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview without renaming")
    args = ap.parse_args()

    vault = args.vault.resolve()
    if not vault.is_dir():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 2

    repo_root = find_repo_root(vault)
    git_available = repo_root is not None and shutil.which("git") is not None

    renames: list[tuple[Path, Path]] = []
    collisions: list[tuple[Path, Path]] = []
    seen = 0
    for file in iter_content_files(vault):
        seen += 1
        if CANONICAL.match(file.name):
            continue
        target = file.with_name(normalized_name(file))
        if target == file:
            continue
        if target.exists():
            collisions.append((file, target))
        else:
            renames.append((file, target))

    if collisions:
        print("Slug collisions — resolve manually:", file=sys.stderr)
        for original, target in collisions:
            print(f"  {original.name} -> {target.name} (already exists)",
                  file=sys.stderr)
        return 1

    if seen == 0:
        # No content files reachable under the passed path — neither loose *.md
        # nor any in a top-level content dir. Almost always a wrong-path
        # invocation; say so explicitly rather than the misleading
        # "All filenames canonical" no-op.
        print(f"No content files found under {vault} "
              f"(expected loose *.md or vault/<content-dir>/*.md). "
              f"Check the path.")
        return 0

    if not renames:
        print("All filenames canonical. Nothing to do.")
        return 0

    for original, target in renames:
        print(f"{original.relative_to(vault)} -> {target.name}")

    if args.dry_run:
        print(f"\n--dry-run: {len(renames)} file(s) would be renamed.")
        return 0

    for original, target in renames:
        if git_available and is_tracked(repo_root, original):
            subprocess.run(
                ["git", "mv",
                 str(original.relative_to(repo_root)),
                 str(target.relative_to(repo_root))],
                cwd=repo_root, check=True,
            )
        else:
            original.rename(target)

    print(f"\nRenamed {len(renames)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
