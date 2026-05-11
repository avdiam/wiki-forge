#!/usr/bin/env python3
"""Regenerate the vault URL index used by the clipper for wake-up dedup.

The index is the precomputed answer to "which URLs has this vault already
seen?" — clipper Step 7 GETs the single file instead of scanning ~110+
markdown files. Index lives at `<vault>/_indexes/url-index.json`; the
`_indexes/` prefix excludes it from ingest by the existing `_`-rule.

Usage:
    # Full regen (called by /wiki-triage after its vault walk):
    python regen_vault_index.py --vault <vault-root>

    # Incremental append after a single new vault file (called by
    # /wiki-capture post-write). Skip-if-URL-present; no-op on duplicate.
    python regen_vault_index.py --vault <vault-root> --append <vault-relative-path>

Writes are atomic via tmp+replace so clipper polls never see a partial file.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import parse_frontmatter


INDEX_REL_PATH = "_indexes/url-index.json"

_SCRIPT_PATH = Path(__file__).resolve()
_WIKI_ROOT = _SCRIPT_PATH.parent.parent
GENERATOR_LABEL = f"{_WIKI_ROOT.name}/{_SCRIPT_PATH.relative_to(_WIKI_ROOT).as_posix()}"


def _entry_from_saved(md: Path, vault_root: Path) -> dict | None:
    fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
    url = (fm.get("url") or "").strip()
    if not url:
        return None
    return {
        "url": url,
        "filename": str(md.relative_to(vault_root)).replace("\\", "/"),
        "status": "saved",
        "clipped": str(fm.get("clipped") or ""),
    }


def _entry_from_skipped(md: Path, vault_root: Path) -> dict | None:
    fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
    url = (fm.get("url") or "").strip()
    if not url:
        return None
    skipped_on = fm.get("skipped_on") or fm.get("date_skipped") or ""
    return {
        "url": url,
        "filename": str(md.relative_to(vault_root)).replace("\\", "/"),
        "status": "skipped",
        "reason": str(fm.get("reason") or ""),
        "skipped_on": str(skipped_on),
    }


def scan_vault(vault_root: Path) -> list[dict]:
    """Walk Web Articles/ + .skipped/, extract one entry per file with a url:."""
    entries: list[dict] = []

    articles_dir = vault_root / "Web Articles"
    if articles_dir.is_dir():
        for md in sorted(articles_dir.glob("*.md")):
            entry = _entry_from_saved(md, vault_root)
            if entry is not None:
                entries.append(entry)

    skipped_dir = vault_root / ".skipped"
    if skipped_dir.is_dir():
        for md in sorted(skipped_dir.glob("*.md")):
            entry = _entry_from_skipped(md, vault_root)
            if entry is not None:
                entries.append(entry)

    return entries


def _build_index(entries: list[dict]) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": GENERATOR_LABEL,
        "entry_count": len(entries),
        "entries": entries,
    }


def _write_atomic(index_path: Path, data: dict) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_name(index_path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(index_path)


def full_regen(vault_root: Path) -> dict:
    entries = scan_vault(vault_root)
    data = _build_index(entries)
    _write_atomic(vault_root / INDEX_REL_PATH, data)
    return data


def append_one(vault_root: Path, rel_path: str) -> tuple[dict, str]:
    """Append a single vault file's entry to the existing index.

    Skip-if-URL-present semantics: duplicate URLs are silently no-op'd
    (action 'skipped-dup'). New URLs append (action 'appended'). Missing
    index falls through to a single-entry fresh write (action 'created').
    """
    target = vault_root / rel_path
    if not target.is_file():
        raise SystemExit(f"--append target not found: {target}")

    if rel_path.replace("\\", "/").startswith(".skipped/"):
        new_entry = _entry_from_skipped(target, vault_root)
    else:
        new_entry = _entry_from_saved(target, vault_root)

    if new_entry is None:
        return ({}, "no-url")

    index_path = vault_root / INDEX_REL_PATH
    if index_path.is_file():
        data = json.loads(index_path.read_text(encoding="utf-8"))
        existing_urls = {e.get("url") for e in data.get("entries", [])}
        if new_entry["url"] in existing_urls:
            return (data, "skipped-dup")
        data["entries"].append(new_entry)
        data["entry_count"] = len(data["entries"])
        data["generated_at"] = datetime.now().isoformat(timespec="seconds")
        _write_atomic(index_path, data)
        return (data, "appended")

    data = _build_index([new_entry])
    _write_atomic(index_path, data)
    return (data, "created")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", required=True, help="Path to vault root")
    parser.add_argument(
        "--append",
        metavar="VAULT_REL_PATH",
        help="Append a single file (vault-relative path, e.g. 'Web Articles/foo.md') "
             "instead of full regen. Skip-if-URL-present.",
    )
    args = parser.parse_args()

    vault_root = Path(args.vault).resolve()
    if not vault_root.is_dir():
        raise SystemExit(f"vault not found: {vault_root}")

    if args.append:
        data, action = append_one(vault_root, args.append)
        if action == "no-url":
            print(f"skipped: {args.append} has no url: frontmatter", file=sys.stderr)
            return 0
        if action == "skipped-dup":
            print(f"skipped: URL already present in index ({args.append})", file=sys.stderr)
            return 0
        print(f"{action}: {args.append} -> {vault_root / INDEX_REL_PATH} "
              f"({data['entry_count']} entries)")
        return 0

    data = full_regen(vault_root)
    print(f"wrote {vault_root / INDEX_REL_PATH}: {data['entry_count']} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
