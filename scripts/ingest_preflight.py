#!/usr/bin/env python3
"""Run every deterministic /wiki-ingest pre-flight check in one call.

Replaces `/wiki-ingest` steps 3–5 and 10.5 as separate model-run commands
(s137, script audit Wave 2): filename normalization, `ingest_source` brief,
content-hash dedup, `pre_screen`, `check_secrets`, the model pick and the
triage-batch lookup. The model keeps only the decisions.

    python ingest_preflight.py --wiki wiki --source "vault/Clippings/2026-11-01-Foo Bar.md"

Writes the full brief (ingest_source JSON + `source_type`, `quality`) to
`<out-dir>/ingest-brief-<slug>.json` (default `.claude/tmp/` under the
project root). The ingestor reads it by path, and `close_operation.py ingest
--brief` consumes and deletes it, so nothing is hand-transcribed.

stdout: one compact JSON object (no preview: the skill reads the source in
full anyway):

    {
      "source": "vault/Clippings/2026-11-01-foo-bar.md",
      "renamed_from": "vault/Clippings/2026-11-01-Foo Bar.md" | null,
      "brief_path": ".claude/tmp/ingest-brief-2026-11-01-foo-bar.json",
      "title": "...", "slug": "...", "word_count": 2140, "sha256_short": "...",
      "merge_mode": false, "existing_summary": null,
      "duplicates": ["pages/sources/<other>.md"],
      "pre_screen": {"verdict": "pass|warn|auto-skip|error", "reasons": [...],
                     "tag_overlap_count": 4, "domain_score": 0.31, "promo_signals": 0},
      "secrets": {"high": 0, "low": 0, "findings": [...]},
      "source_type": "article" | null, "quality": "listicle" | null,
      "model": "sonnet|opus",
      "triage": {"file": "dev/ingest-triage-....md", "batch": 3} | null,
      "gates": ["merge_mode", "duplicate", "pre_screen_warn", "pre_screen_auto_skip",
                "secrets_high", "secrets_low", "renamed"]
    }

`gates` lists every condition the skill must surface or decide on; an empty
list means proceed straight to reading the source.

Exit codes: 0 = report emitted (gates may still need a decision);
1 = halt (slug collision on rename: resolve manually); 2 = bad input, or a
path the ingest-exclusion rule forbids (`.`/`_`-prefixed vault segment,
`not_a_source: true`); excluded files are refused before any rename.
Stdlib only; Python 3.9-safe.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_secrets import load_patterns, scan  # noqa: E402
from frontmatter import parse_frontmatter  # noqa: E402
from ingest_source import build_brief  # noqa: E402
from normalize_vault import CANONICAL, _excluded, normalized_name  # noqa: E402
from pre_screen import pre_screen  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SONNET_WORD_THRESHOLD = 1200
MAX_FINDINGS_SHOWN = 5


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def exclusion_reason(source: Path, project_root: Path) -> str | None:
    """Ingest-exclusion rule (root AGENTS.md § Vault): `.`/`_`-prefixed vault
    path segments and `not_a_source: true` files are never sources. Checked
    BEFORE the rename, so protocol files are never touched."""
    try:
        parts = source.resolve().relative_to((project_root / "vault").resolve()).parts
    except ValueError:
        parts = (source.name,)
    if any(_excluded(seg) for seg in parts):
        return "path segment starts with '.' or '_' (protocol/metadata, not a source)"
    try:
        fm = parse_frontmatter(source.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        fm = {}
    if str(fm.get("not_a_source", "")).lower() == "true":
        return "frontmatter has not_a_source: true"
    return None


def normalize(source: Path, project_root: Path) -> tuple[Path, Path | None]:
    """Rename one non-canonical vault file. Returns (path, original|None). Raises FileExistsError on collision."""
    if CANONICAL.match(source.name):
        return source, None
    target = source.with_name(normalized_name(source))
    if target == source:
        return source, None
    if target.exists():
        raise FileExistsError(f"{source.name} -> {target.name} (already exists)")
    tracked = subprocess.run(["git", "-C", str(project_root), "ls-files", "--error-unmatch", str(source.resolve())],
                             capture_output=True).returncode == 0
    if tracked:
        subprocess.run(["git", "-C", str(project_root), "mv", str(source.resolve()), str(target.resolve())],
                       check=True, capture_output=True)
    else:
        source.rename(target)
    return target, source


def find_duplicates(wiki: Path, sha: str, existing: str | None) -> list:
    if not sha:
        return []
    needle = re.compile(rf"^source_hash:\s*['\"]?{re.escape(sha)}['\"]?\s*$", re.MULTILINE)
    hits = []
    for page in sorted((wiki / "pages" / "sources").glob("*.md")):
        relp = f"pages/sources/{page.name}"
        if relp == existing:
            continue  # the source's own summary: that's merge_mode, not a duplicate
        try:
            head = page.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if needle.search(head):
            hits.append(relp)
    return hits


def triage_membership(project_root: Path, slug: str) -> dict | None:
    dev = project_root / "dev"
    if not dev.is_dir():
        return None
    files = sorted(p for p in dev.glob("ingest-triage-*.md") if "archived" not in p.name and "superseded" not in p.name)
    if not files:
        return None
    text = files[-1].read_text(encoding="utf-8", errors="replace")
    fm = re.search(r"\A---\n(.*?)\n---", text, re.DOTALL)
    if not fm or not re.search(r"^status:\s*active\b", fm.group(1), re.MULTILINE | re.IGNORECASE):
        return None
    m = re.search(rf"^- \[ \] \*\*`{re.escape(slug)}(?:\.md)?`\*\*", text, re.MULTILINE)
    if not m:
        return None
    batch = None
    for sec in re.finditer(r"^## Batch (\d+)\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL):
        if sec.start() <= m.start() < sec.end():
            batch = int(sec.group(1))
    return {"file": rel(files[-1], project_root), "batch": batch}


def run(wiki: Path, source: Path, out_dir: Path | None) -> tuple[int, dict]:
    project_root = wiki.parent
    if not source.is_absolute():
        source = (Path.cwd() / source)
    if not source.is_file():
        return 2, {"error": f"source not found: {source}"}
    why = exclusion_reason(source, project_root)
    if why:
        return 2, {"error": f"excluded from ingest: {why}", "source": rel(source, project_root)}

    try:
        source, original = normalize(source, project_root)
    except FileExistsError as e:
        return 1, {"error": f"slug collision on rename, resolve manually: {e}", "source": rel(source, project_root)}

    brief = build_brief(wiki, source.resolve())
    fm = parse_frontmatter(source.read_text(encoding="utf-8", errors="replace"))
    quality = str(fm.get("quality") or "").strip().lower() or None
    source_type = str(fm.get("source_type") or "").strip().lower() or None
    brief["source_path"] = rel(source, project_root)
    brief["source_type"] = source_type
    brief["quality"] = quality

    screen = pre_screen(wiki, source.resolve())
    findings = scan(source, load_patterns(wiki))
    high = [f for f in findings if f.get("severity") == "high"]
    low = [f for f in findings if f.get("severity") != "high"]
    dups = find_duplicates(wiki, brief.get("sha256_short") or "", brief.get("existing_summary"))
    slug = brief["suggested_slug"]

    out_dir = out_dir or (project_root / ".claude" / "tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    brief_path = out_dir / f"ingest-brief-{slug}.json"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    gates = []
    if brief.get("merge_mode"):
        gates.append("merge_mode")
    if dups:
        gates.append("duplicate")
    verdict = screen.get("verdict")
    if verdict == "warn":
        gates.append("pre_screen_warn")
    elif verdict == "auto-skip":
        gates.append("pre_screen_auto_skip")
    elif verdict == "error":
        gates.append("pre_screen_error")
    if high:
        gates.append("secrets_high")
    elif low:
        gates.append("secrets_low")
    if original is not None:
        gates.append("renamed")

    words = brief.get("word_count") or 0
    report = {
        "source": brief["source_path"],
        "renamed_from": rel(original, project_root) if original is not None else None,
        "brief_path": rel(brief_path, project_root),
        "title": brief.get("title_guess"),
        "slug": slug,
        "word_count": words,
        "sha256_short": brief.get("sha256_short"),
        "merge_mode": brief.get("merge_mode", False),
        "existing_summary": brief.get("existing_summary"),
        "duplicates": dups,
        "pre_screen": {k: screen.get(k) for k in ("verdict", "reasons", "tag_overlap_count", "domain_score",
                                                  "promo_signals", "author_trust") if k in screen},
        "secrets": {"high": len(high), "low": len(low), "findings": (high + low)[:MAX_FINDINGS_SHOWN]},
        "source_type": source_type,
        "quality": quality,
        "model": "sonnet" if quality == "listicle" or words < SONNET_WORD_THRESHOLD else "opus",
        "triage": triage_membership(project_root, slug),
        "gates": gates,
    }
    return 0, report


def main(argv: list) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wiki", required=True, help="Wiki root")
    p.add_argument("--source", required=True, help="Raw vault file")
    p.add_argument("--out-dir", default=None, help="Brief directory (default <project>/.claude/tmp)")
    a = p.parse_args(argv)
    wiki = Path(a.wiki).resolve()
    if not (wiki / "CLAUDE.md").is_file():
        print(json.dumps({"error": f"{wiki} is not a wiki root (no CLAUDE.md)"}))
        return 2
    code, report = run(wiki, Path(a.source), Path(a.out_dir) if a.out_dir else None)
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
