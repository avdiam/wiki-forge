#!/usr/bin/env python3
"""Compose the drift re-capture queue from source_freshness.jsonl.

Reads `wiki/.meta/source_freshness.jsonl`, identifies slugs whose most-recent
poll row shows `drifted: true` AND whose source page still has the
`source_drifted: true` frontmatter flag (i.e., not yet cleared by a previous
re-ingest), then splits them into two re-capture buckets by URL class
(`url_classifier_lists.json`, same classifier `/wiki-capture` uses):

- **recapture** — fetchable domains. Re-capture each with `/wiki-capture
  <url>`; the chained `/wiki-ingest` sees `source_drifted: true` on the
  existing source page and runs the drift-triggered re-ingest (clears the
  flag, refreshes `source_hash:`).
- **clip** — `auth_required` domains. Only the user's authenticated browser
  can fetch these; they are handed over via `clip_handoff.py` (<= 15 URLs ->
  offer to open them as Chrome tabs; more -> an HTML checklist). The user
  clips with the Obsidian Web Clipper, then each new `vault/Clippings/` file
  is ingested as the drift re-capture.

(`check_source_freshness.py` skips auth_required domains when polling, so the
clip bucket is normally empty — it fills only when a domain moves onto the
auth_required list after its baseline was recorded.)

Pure stdlib. No PyYAML, no requests.

Usage:
    python compose_drift_queue.py <wiki-root>                  # markdown report
    python compose_drift_queue.py <wiki-root> --json           # bucket JSON
    python compose_drift_queue.py <wiki-root> --handoff [--out PATH]
        # JSON incl. the clip_handoff decision for the clip bucket

Exit codes:
    0 — queue composed
    1 — no drift to queue (script ran cleanly, just nothing to do)
    2 — fatal: not a wiki root, no jsonl, etc.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from frontmatter import parse_frontmatter  # noqa: E402
from extract_page_metadata import _load_lists, classify  # noqa: E402
import clip_handoff  # noqa: E402



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
            # by including the record rather than silently dropping it.
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


def split_buckets(records: list[dict]) -> dict[str, list[dict]]:
    """Split drifted records into `recapture` (fetchable) and `clip` (auth_required)."""
    head_preserving, auth_required = _load_lists()
    buckets: dict[str, list[dict]] = {"recapture": [], "clip": []}
    for rec in records:
        cls, _host = classify(rec["url"], head_preserving, auth_required) if rec["url"] else ("default", "")
        buckets["clip" if cls == "auth_required" else "recapture"].append(rec)
    return buckets


def render_body(buckets: dict[str, list[dict]]) -> str:
    """Render the human-readable drift report."""
    n = len(buckets["recapture"]) + len(buckets["clip"])
    lines = [f"Drift detected by `check_source_freshness.py` on {n} source page{'s' if n != 1 else ''}.", ""]

    def table(rows: list[dict]) -> list[str]:
        out = ["| Slug | URL | baseline_at | current_at |", "|---|---|---|---|"]
        out += [f"| {r['slug']} | {r['url']} | {r['baseline_at']} | {r['current_at']} |" for r in rows]
        return out + [""]

    if buckets["recapture"]:
        lines += ["## Re-capture with `/wiki-capture` (fetchable)", ""] + table(buckets["recapture"])
    if buckets["clip"]:
        lines += ["## Clip in the browser (auth-required — hand over via `clip_handoff.py`)", ""] + table(buckets["clip"])
    lines.append(
        "`baseline_at` is the first-poll timestamp recorded for the slug; `current_at` is the "
        "polling run that flagged the drift. Each re-capture lands as a new vault file; ingesting "
        "it runs the drift-triggered re-ingest, which clears `source_drifted: true`."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Compose the drift re-capture queue.")
    p.add_argument("wiki", help="Path to the wiki root")
    p.add_argument("--json", action="store_true", help="Emit bucket JSON instead of markdown")
    p.add_argument("--handoff", action="store_true",
                   help="Emit JSON incl. the clip_handoff decision for the clip bucket")
    p.add_argument("--out", default=None, help="HTML path for an over-threshold clip handoff")
    p.add_argument("--threshold", type=int, default=clip_handoff.DEFAULT_THRESHOLD,
                   help="clip_handoff tab threshold")
    args = p.parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root", file=sys.stderr)
        return 2

    jsonl_path = wiki_root / ".meta" / "source_freshness.jsonl"
    if not jsonl_path.is_file():
        print(f"error: {jsonl_path} not found — run check_source_freshness.py first", file=sys.stderr)
        return 2

    records = filter_uncleared(collect_drifted(jsonl_path), wiki_root)
    if not records:
        print("no drift to queue", file=sys.stderr)
        return 1

    buckets = split_buckets(records)
    if not (args.json or args.handoff):
        sys.stdout.write(render_body(buckets))
        return 0

    payload: dict = dict(buckets)
    if args.handoff:
        urls = clip_handoff.dedupe([r["url"] for r in buckets["clip"]])
        mode = clip_handoff.decide(urls, args.threshold)
        html_path = None
        if mode == "html":
            out = Path(args.out) if args.out else clip_handoff.DEFAULT_OUT_DIR / f"clip-queue-drift-{date.today().isoformat()}.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(clip_handoff.render_html(urls, "Drift re-capture — clip queue"), encoding="utf-8")
            html_path = str(out)
        payload["clip_handoff"] = {"mode": mode, "count": len(urls), "urls": urls, "html_path": html_path}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
