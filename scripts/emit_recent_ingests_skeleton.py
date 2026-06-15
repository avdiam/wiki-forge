#!/usr/bin/env python3
"""Emit a Mode A '## Ingests N–M' bullet skeleton into wiki/overview.md.

The wiki-curator subagent then fills the <SYNTHESIS> placeholders and
the <SECTION-TITLE> token. This script is the mechanical half of Mode A —
the template-shaped components (ingest number, source title, author
wikilink, date, created/updated page lists) come from `log.md`'s
`## [...] ingest |` entries joined to source-page frontmatter; the
synthesis sentence + section header are LLM judgment.

`log.md` is authoritative for ingest numbering (the canonical `#N` counter
matches `grep -c '^## \\[.*\\] ingest ' wiki/log.md`). `.meta/ingest_log.jsonl`
is NOT used here — it has gaps from pre-jsonl-era ingests and
skipped/error rows, so its row indices don't align with `#N`.

Idempotent: re-running with the same --from/--to replaces the previously
injected section (matched by `## Ingests N–M` header) rather than
appending a duplicate. Section is inserted just before `## Stats` when no
prior occurrence is found.

Usage:
    python emit_recent_ingests_skeleton.py --wiki wiki --from 213 --to 221
    python emit_recent_ingests_skeleton.py --wiki wiki --from 213 --to 221 --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
AUTHOR_BODY_RE = re.compile(r"^\*\*Author:\*\*\s*(.+)$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
INGEST_HEADER_RE = re.compile(
    r"^## \[(?P<ts>[^\]]+)\] ingest \| (?P<title>.+?)\s*$"
)
FIELD_RE = re.compile(r"^- (?P<key>Created|Updated|Commit|Note):\s*(?P<val>.*)$")
DATE_SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
SECTION_PLACEHOLDER = "<SECTION-TITLE>"
SYNTHESIS_PLACEHOLDER = "<SYNTHESIS>"


def parse_yaml_simple(block: str) -> dict:
    """Minimal scalar/flow-list YAML scanner — avoids PyYAML dep.

    Handles `key: value`, `key: "quoted"`, and `key: [a, b, "c"]`. Multi-line
    block lists (`key:\\n  - item`) are ignored — none of the fields this
    script reads use that form.
    """
    out: dict = {}
    for line in block.splitlines():
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if not val:
            continue
        if val.startswith("[") and val.endswith("]"):
            items = [
                v.strip().strip('"').strip("'")
                for v in val[1:-1].split(",")
                if v.strip()
            ]
            out[key] = items
        elif (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            out[key] = val[1:-1]
        else:
            out[key] = val
    return out


def parse_log_ingests(log_md: Path) -> list[dict]:
    """Return ingest entries in lifetime order. Each: {ts, title, created, updated, commit}."""
    text = log_md.read_text(encoding="utf-8")
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = INGEST_HEADER_RE.match(line)
        if m:
            if current is not None:
                entries.append(current)
            current = {
                "ts": m.group("ts"),
                "title": m.group("title"),
                "created": [],
                "updated": [],
                "commit": "",
            }
            continue
        if current is None:
            continue
        # If we hit a non-ingest header, close out current
        if line.startswith("## "):
            entries.append(current)
            current = None
            continue
        fm = FIELD_RE.match(line)
        if not fm:
            continue
        key, val = fm.group("key"), fm.group("val").strip()
        if key in ("Created", "Updated"):
            items = [v.strip() for v in val.split(",") if v.strip()]
            current[key.lower()] = items
        elif key == "Commit":
            current["commit"] = val
    if current is not None:
        entries.append(current)
    return entries


def page_slug(page_path: str) -> str:
    """`pages/sources/2026-04-15-foo.md` or `sources/2026-04-15-foo` → `2026-04-15-foo`."""
    s = page_path
    if s.endswith(".md"):
        s = s[:-3]
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    return s


def find_source_slug(created: list[str]) -> str | None:
    """First Created item whose slug starts with a date prefix."""
    for item in created:
        slug = page_slug(item)
        if DATE_SLUG_RE.match(slug):
            return slug
    return None


def load_source_page(wiki: Path, source_slug: str) -> tuple[dict, str]:
    path = wiki / "pages" / "sources" / f"{source_slug}.md"
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    fm = parse_yaml_simple(m.group(1)) if m else {}
    body = text[m.end():] if m else text
    return fm, body


def author_attribution(body: str, wiki: Path) -> str:
    """Render the author for the bullet's `(author, date)` attribution.

    Three resolution paths in priority order:
    1. `**Author:** [[slug|Display]]` → `[[slug]]` (strip display alias).
    2. `**Author:** Plain Name` → if the kebab-cased slug exists at
       `pages/entities/<slug>.md`, emit `[[<slug>]]`; otherwise emit the
       plain name as-is (no wikilink — curator can promote during synthesis).
    3. No `**Author:**` line → empty string (caller emits `(date)` only).

    Path 2's existence check avoids fabricating wikilinks to non-existent
    entity pages — the same discipline `check_wikilinks.py` enforces.
    """
    m = AUTHOR_BODY_RE.search(body)
    if not m:
        return ""
    line = m.group(1).strip()
    wl = WIKILINK_RE.search(line)
    if wl:
        return f"[[{wl.group(1)}]]"
    # Plain-text name — kebab-case and check entity existence
    name = line.split("(")[0].strip()  # drop trailing parenthetical if any
    if not name:
        return ""
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"\s+", "-", slug.strip())
    entity_path = wiki / "pages" / "entities" / f"{slug}.md"
    if entity_path.exists():
        return f"[[{slug}]]"
    return name


def format_pages(pages: list[str]) -> str:
    return ", ".join(f"[[{page_slug(p)}]]" for p in pages if p)


def build_bullet(ingest_num: int, entry: dict, fm: dict, body: str, wiki: Path) -> str:
    title = str(fm.get("title", entry["title"])).strip('"').strip("'")
    author = author_attribution(body, wiki)
    date = (
        fm.get("published")
        or fm.get("captured")
        or entry["ts"].split()[0]
    )
    created = format_pages(entry.get("created", []))
    updated = format_pages(entry.get("updated", []))

    attribution = f"({author}, {date})" if author else f"({date})"
    parts = [f"- **#{ingest_num} — {title} {attribution}.**"]
    if created:
        parts.append(f" Created {created};")
    if updated:
        parts.append(f" updated {updated};")
    parts.append(f" {SYNTHESIS_PLACEHOLDER}.")
    return "".join(parts)


def build_section(wiki: Path, n_from: int, n_to: int) -> str:
    log_md = wiki / "log.md"
    if not log_md.exists():
        sys.exit(f"log.md not found at {log_md}")
    entries = parse_log_ingests(log_md)
    if n_from < 1 or n_to > len(entries) or n_from > n_to:
        sys.exit(
            f"window {n_from}–{n_to} out of range "
            f"(log.md has {len(entries)} ingest entries)"
        )

    bullets: list[str] = []
    for i, entry in enumerate(entries[n_from - 1 : n_to]):
        ingest_num = n_from + i
        source_slug = find_source_slug(entry.get("created", []))
        fm, body = load_source_page(wiki, source_slug) if source_slug else ({}, "")
        bullets.append(build_bullet(ingest_num, entry, fm, body, wiki))

    header = f"## Ingests {n_from}–{n_to} — {SECTION_PLACEHOLDER}"
    return header + "\n\n" + "\n".join(bullets) + "\n"


def inject(overview: Path, section: str, n_from: int, n_to: int) -> tuple[str, str]:
    """Return (action, new_text). action ∈ {'replaced', 'inserted', 'unchanged'}."""
    text = overview.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## Ingests {n_from}[–\-]{n_to}\b.*?(?=^## |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    if pattern.search(text):
        new_text = pattern.sub(section.rstrip() + "\n\n", text, count=1)
        action = "replaced"
    else:
        anchor = "## Stats"
        idx = text.find(anchor)
        if idx == -1:
            sys.exit("overview.md missing '## Stats' anchor — cannot place skeleton")
        new_text = text[:idx] + section + "\n" + text[idx:]
        action = "inserted"

    if new_text == text:
        return "unchanged", text
    return action, new_text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wiki", default="wiki", help="wiki root (default: wiki)")
    ap.add_argument("--from", dest="n_from", type=int, required=True)
    ap.add_argument("--to", dest="n_to", type=int, required=True)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the skeleton to stdout; do not modify overview.md",
    )
    args = ap.parse_args()

    wiki = Path(args.wiki).resolve()
    section = build_section(wiki, args.n_from, args.n_to)

    if args.dry_run:
        print(section)
        return

    overview = wiki / "overview.md"
    if not overview.exists():
        sys.exit(f"overview.md not found at {overview}")

    action, new_text = inject(overview, section, args.n_from, args.n_to)
    if action == "unchanged":
        print(
            f"overview.md unchanged — skeleton for #{args.n_from}–#{args.n_to} "
            "already matches existing section"
        )
        return

    overview.write_text(new_text, encoding="utf-8")
    print(
        f"{action} ## Ingests {args.n_from}–{args.n_to} skeleton "
        f"({args.n_to - args.n_from + 1} bullets) in {overview}"
    )


if __name__ == "__main__":
    main()
