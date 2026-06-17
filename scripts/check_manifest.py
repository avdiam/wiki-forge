#!/usr/bin/env python3
"""Assert the plugin manifest's advertised inventory matches what ships on disk.

`wiki-forge/.claude-plugin/plugin.json`'s `description` hand-maintains a count +
name list of the skills and subagents the plugin ships:

    Ships seventeen skills (capture, ingest, …, init) and four subagents
    (ingestor, librarian, linter, curator), plus …

Nothing checked that prose against the actual `wiki-forge/skills/*/` and
`wiki-forge/agents/*.md` trees, so it silently drifted five skills out of date
(advertised "twelve", shipped 17) before session 114 caught it by eye — the same
silent-inventory-drift class as the dev/reports dashboards, but mechanically
checkable. This script closes that gap.

For each of the two clauses it asserts, in either direction:
  1. the count word ("seventeen") equals the parenthesized list length, AND
  2. the parenthesized name set equals the on-disk set.
(Count-vs-disk falls out transitively when both hold.)

Disk names are compared with the uniform `wiki-` prefix stripped, since the
directories are `wiki-capture`, `wiki-init`, … while the manifest lists the bare
`capture`, `init`, … forms.

`check_manifest()` returns a list of issue dicts (`{"kind", "detail"}`); an empty
list means the manifest is in sync. The accompanying `dev/tests/test_manifest.py`
runs it against the live tree as a regression guard, so the next skill ship
can't re-drift the manifest without the suite going red.

Run:  python wiki-forge/scripts/check_manifest.py
Exit codes: 0 — manifest in sync; 1 — mismatch(es) detected.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Windows-friendly: the report uses → / ≠ glyphs that crash on cp1252/cp1253.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ---------- number-word parsing (manifest writes counts as words) ----------

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def word_to_int(word: str) -> int | None:
    """Parse an English number word (0–99) to int, or None if unrecognized."""
    w = word.strip().lower()
    if w in _ONES:
        return _ONES.index(w)
    parts = w.replace("-", " ").split()
    if len(parts) == 1 and parts[0] in _TENS:
        return _TENS[parts[0]]
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES[1:10]:
        return _TENS[parts[0]] + _ONES.index(parts[1])
    return None


# ---------- manifest parsing ----------

_CLAUSE_RE = {
    "skills": re.compile(r"\b([A-Za-z-]+)\s+skills\s*\(([^)]*)\)", re.I),
    "subagents": re.compile(r"\b([A-Za-z-]+)\s+subagents\s*\(([^)]*)\)", re.I),
}


def _parse_clause(description: str, kind: str) -> tuple[str, list[str]] | None:
    """Return (count_word, names) for a 'Ships <word> <kind> (a, b, …)' clause."""
    m = _CLAUSE_RE[kind].search(description)
    if not m:
        return None
    count_word = m.group(1)
    names = [n.strip().lower() for n in m.group(2).split(",") if n.strip()]
    return count_word, names


# ---------- on-disk inventory ----------

def _strip_prefix(name: str) -> str:
    return name[len("wiki-"):] if name.startswith("wiki-") else name


def disk_skills(forge_root: Path) -> set[str]:
    """Bare skill names on disk (wiki- prefix stripped)."""
    skills_dir = forge_root / "skills"
    if not skills_dir.is_dir():
        return set()
    return {
        _strip_prefix(d.name)
        for d in skills_dir.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
    }


def disk_subagents(forge_root: Path) -> set[str]:
    """Bare subagent names on disk (wiki- prefix + .md stripped)."""
    agents_dir = forge_root / "agents"
    if not agents_dir.is_dir():
        return set()
    return {
        _strip_prefix(f.stem)
        for f in agents_dir.glob("*.md")
        if not f.name.startswith(("_", "."))
    }


# ---------- the check ----------

def _resolve_forge_root() -> Path:
    # This script lives at <wiki-forge>/scripts/check_manifest.py in both the
    # live repo (wiki-forge/scripts/) and a standalone plugin checkout, so the
    # plugin tree is always one level up from the scripts dir.
    return Path(__file__).resolve().parents[1]


def check_manifest(forge_root: Path | None = None) -> list[dict]:
    """Compare the manifest's advertised inventory to disk. [] == in sync."""
    root = forge_root if forge_root is not None else _resolve_forge_root()
    manifest_path = root / ".claude-plugin" / "plugin.json"
    if not manifest_path.is_file():
        return [{"kind": "missing_manifest", "detail": f"{manifest_path} not found"}]

    try:
        description = json.loads(manifest_path.read_text(encoding="utf-8")).get("description", "")
    except (json.JSONDecodeError, OSError) as exc:
        return [{"kind": "unparseable_manifest", "detail": str(exc)}]

    issues: list[dict] = []
    for kind, on_disk in (("skills", disk_skills(root)), ("subagents", disk_subagents(root))):
        parsed = _parse_clause(description, kind)
        if parsed is None:
            issues.append({"kind": f"unparseable_{kind}", "detail": f"no '<word> {kind} (…)' clause in description"})
            continue
        count_word, names = parsed
        advertised = set(names)

        count = word_to_int(count_word)
        if count is None:
            issues.append({"kind": f"bad_count_word_{kind}", "detail": f"count word '{count_word}' is not a recognized number"})
        elif count != len(names):
            issues.append({"kind": f"{kind}_count_mismatch", "detail": f"count word '{count_word}' ({count}) != advertised list length {len(names)}"})

        if advertised != on_disk:
            missing = sorted(on_disk - advertised)  # on disk, not advertised
            extra = sorted(advertised - on_disk)     # advertised, not on disk
            parts = []
            if missing:
                parts.append(f"on disk but not advertised: {', '.join(missing)}")
            if extra:
                parts.append(f"advertised but not on disk: {', '.join(extra)}")
            issues.append({"kind": f"{kind}_disk_mismatch", "detail": "; ".join(parts)})

    return issues


def main() -> int:
    issues = check_manifest()
    if not issues:
        print("Manifest inventory in sync with disk (skills + subagents).")
        return 0
    print("Manifest drift detected:")
    for i in issues:
        print(f"  {i['kind']}: {i['detail']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
