#!/usr/bin/env python3
"""Compose the `[DRIFT QUEUE]` mailbox message body from source_freshness.jsonl.

Reads `wiki/.meta/source_freshness.jsonl`, identifies slugs whose most-recent
poll row shows `drifted: true` AND whose source page still has the
`source_drifted: true` frontmatter flag (i.e., not yet cleared by a previous
re-ingest), then emits either:

- The markdown body content (default) — for inline copy into a mailbox file.
- A complete mailbox file at `vault/_mailbox/wiki_message_<N+1>.md` when
  invoked with `--write`. Handles the global counter scan across top-level
  AND the `.archive/` subdirectory per `vault/_mailbox/README.md`.

Removes the failure mode where wiki-Claude composes the message manually
and could misread the lint state into a 0-row mail. Per Step 9 of
`vault/.clipper-rules.md` and Q5 of the lifecycle-subsystem design lock.

Pure stdlib. No PyYAML, no requests.

Usage:
    python compose_drift_queue.py <wiki-root>                # print body to stdout
    python compose_drift_queue.py <wiki-root> --write        # write mailbox file
    python compose_drift_queue.py <wiki-root> --vault <path> # override vault location

Exit codes:
    0 — message composed and printed (or `--write` succeeded)
    1 — no drift to queue (script ran cleanly, just nothing to do)
    2 — fatal: not a wiki root, no jsonl, etc.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from frontmatter import parse_frontmatter  # noqa: E402


COUNTER_RE = re.compile(r"^(?:wiki|chrome)_message_(\d+)\.md$")


def collect_drifted(jsonl_path: Path) -> list[dict]:
    """Return per-slug drift records from the freshness JSONL.

    Iterates rows, groups by slug, and for each slug records:
      - first_polled_at  — baseline_at (first-ever row's polled_at)
      - latest_row       — the most recent row for the slug

    A slug is "drifted" iff its latest row has drifted=true.
    """
    if not jsonl_path.is_file():
        return []
    first_polled: dict[str, str] = {}
    latest: dict[str, dict] = {}
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slug = row.get("slug")
            if not isinstance(slug, str):
                continue
            polled_at = row.get("polled_at")
            if isinstance(polled_at, str) and slug not in first_polled:
                first_polled[slug] = polled_at
            latest[slug] = row
    drifted: list[dict] = []
    for slug, row in latest.items():
        if not row.get("drifted"):
            continue
        drifted.append({
            "slug": slug,
            "url": row.get("url") or "",
            "baseline_at": first_polled.get(slug, ""),
            "current_at": row.get("polled_at", ""),
        })
    drifted.sort(key=lambda r: r["slug"])
    return drifted


def filter_uncleared(records: list[dict], wiki_root: Path) -> list[dict]:
    """Drop slugs whose source page no longer has `source_drifted: true`.

    A slug's flag is cleared by the wiki-ingestor's drift-triggered re-ingest
    addendum after a successful re-capture. The JSONL row's `drifted: true`
    persists across the cycle, so the source-page check is what distinguishes
    "still pending" from "already round-tripped."
    """
    sources_dir = wiki_root / "pages" / "sources"
    out: list[dict] = []
    for rec in records:
        page = sources_dir / f"{rec['slug']}.md"
        if not page.is_file():
            # Source page missing — could be an in-flight rename. Surface it
            # by including the record; the clipper's `vault_file_missing`
            # edge case handles the actual file gracefully.
            out.append(rec)
            continue
        try:
            fm = parse_frontmatter(page.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            out.append(rec)
            continue
        if fm.get("source_drifted") is True:
            out.append(rec)
    return out


def render_body(records: list[dict]) -> str:
    """Render the message body — the `## Drifted sources` table + framing."""
    n = len(records)
    lines = [
        "Drift detected by `check_source_freshness.py` on the following source pages. Re-fetch each per Step 9 of `.clipper-rules.md`, overwrite `vault/Web Articles/<slug>.md`, and reply with per-slug status.",
        "",
        "## Drifted sources",
        "",
        "| Slug | URL | baseline_at | current_at |",
        "|---|---|---|---|",
    ]
    for rec in records:
        lines.append(
            f"| {rec['slug']} | {rec['url']} | {rec['baseline_at']} | {rec['current_at']} |"
        )
    lines.append("")
    lines.append(
        f"`baseline_at` is the first-poll timestamp wiki recorded for the slug; "
        f"`current_at` is the polling run that flagged the drift. Per-slug "
        f"freshness check: if `vault/Web Articles/<slug>.md`'s `clipped:` is "
        f"newer than `current_at`, you've already re-captured — record "
        f"`already_fresh` and move on. Total drifted: {n}."
    )
    return "\n".join(lines) + "\n"


def next_message_counter(mailbox_dir: Path) -> int:
    """Scan top-level + .archive/** for the highest *_message_NNN counter."""
    if not mailbox_dir.is_dir():
        return 1
    max_n = 0
    # Top-level
    for p in mailbox_dir.iterdir():
        if not p.is_file():
            continue
        m = COUNTER_RE.match(p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    # Archive (recursive)
    archive = mailbox_dir / ".archive"
    if archive.is_dir():
        for p in archive.rglob("*.md"):
            m = COUNTER_RE.match(p.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def render_mailbox_file(records: list[dict], counter: int) -> tuple[str, str]:
    """Return (filename, full-message-content) for the mailbox write."""
    today = date.today().isoformat()
    n = len(records)
    subject = f'"[DRIFT QUEUE] {n} source{"s" if n != 1 else ""} drifted — re-capture via Obsidian REST API"'
    header = (
        "---\n"
        "from: wiki\n"
        "to: clipper\n"
        f"date: {today}\n"
        "status: open\n"
        "re: null\n"
        f"subject: {subject}\n"
        "---\n"
        "\n"
    )
    filename = f"wiki_message_{counter:03d}.md"
    return filename, header + render_body(records)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Compose [DRIFT QUEUE] mailbox message.")
    p.add_argument("wiki", help="Path to the wiki root")
    p.add_argument("--write", action="store_true",
                   help="Write a mailbox file under vault/_mailbox/ instead of printing to stdout")
    p.add_argument("--vault", default=None,
                   help="Override vault directory (default: <wiki>/../vault)")
    args = p.parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root", file=sys.stderr)
        return 2

    jsonl_path = wiki_root / ".meta" / "source_freshness.jsonl"
    if not jsonl_path.is_file():
        print(f"error: {jsonl_path} not found — run check_source_freshness.py first", file=sys.stderr)
        return 2

    vault_root = Path(args.vault).resolve() if args.vault else (wiki_root.parent / "vault")
    if not vault_root.is_dir():
        print(f"error: vault dir not found at {vault_root}", file=sys.stderr)
        return 2

    candidates = collect_drifted(jsonl_path)
    records = filter_uncleared(candidates, wiki_root)

    if not records:
        print("no drift to queue", file=sys.stderr)
        return 1

    if not args.write:
        sys.stdout.write(render_body(records))
        return 0

    mailbox_dir = vault_root / "_mailbox"
    if not mailbox_dir.is_dir():
        print(f"error: mailbox dir not found at {mailbox_dir}", file=sys.stderr)
        return 2
    counter = next_message_counter(mailbox_dir)
    filename, content = render_mailbox_file(records, counter)
    target = mailbox_dir / filename
    if target.exists():
        print(f"error: {target} already exists — counter collision; rerun", file=sys.stderr)
        return 2
    target.write_text(content, encoding="utf-8")
    try:
        display = target.relative_to(wiki_root.parent)
    except ValueError:
        display = target
    print(f"wrote {display} ({len(records)} drifted slug{'s' if len(records) != 1 else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
