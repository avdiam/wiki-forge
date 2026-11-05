#!/usr/bin/env python3
"""Hand a list of browser-only URLs to the user for manual clipping.

Some pages can only be captured from the user's authenticated browser
(paywalls, Cloudflare bot-fight, login walls — the `auth_required` list in
`url_classifier_lists.json`). The wiki never fetches those itself; the user
clips them with the Obsidian Web Clipper extension, which drops the files
into `vault/Clippings/`. This script decides HOW the list is handed over:

- **<= threshold URLs (default 15) -> `open_tabs`.** The session offers to
  open each URL as a Chrome tab (claude-in-chrome `tabs_create_mcp` +
  `navigate`) so the user can clip them one by one. Opening tabs is an
  offer — the session asks before doing it.
- **> threshold URLs -> `html`.** Writes a self-contained HTML checklist
  (numbered links, each opening in a new tab, with a checkbox per row) and
  returns its path. Too many tabs at once is unusable; a page the user works
  down at their own pace is not.
- **0 URLs -> `empty`.** Nothing to hand over.

URLs are de-duplicated preserving first-seen order.

Input (any combination, merged in this order): positional URLs, `--from-json
FILE` (a JSON list of strings, or of objects with a `url` key — the shape
`batch_capture.py` and `compose_drift_queue.py --json` emit), and `-` to read
one URL per line from stdin.

Output: one JSON object on stdout:
    {"mode": "open_tabs"|"html"|"empty", "count": N, "threshold": T,
     "urls": [...], "html_path": "<path>" | null}

Pure stdlib.

Usage:
    python clip_handoff.py URL [URL ...]
    python clip_handoff.py --from-json bucket.json --title "Batch 2026-11-05"
    python clip_handoff.py - < urls.txt
    python clip_handoff.py --threshold 15 --out .claude/tmp/clip-queue.html URL ...

Exit codes:
    0 — handoff decided (including `empty`)
    2 — bad input (unreadable --from-json, malformed JSON)
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_THRESHOLD = 15
DEFAULT_OUT_DIR = Path(".claude") / "tmp"


def _urls_from_json(data) -> list[str]:
    out: list[str] = []
    if not isinstance(data, list):
        raise ValueError("expected a JSON list")
    for item in data:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("url"), str):
            out.append(item["url"])
    return out


def dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def decide(urls: list[str], threshold: int = DEFAULT_THRESHOLD) -> str:
    if not urls:
        return "empty"
    return "open_tabs" if len(urls) <= threshold else "html"


def render_html(urls: list[str], title: str) -> str:
    rows = "\n".join(
        f'    <li><label><input type="checkbox"> '
        f'<a href="{html.escape(u, quote=True)}" target="_blank" rel="noopener">'
        f"{html.escape(u)}</a></label></li>"
        for u in urls
    )
    t = html.escape(title)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{t}</title>\n"
        "<style>\n"
        "  :root { color-scheme: light dark; }\n"
        "  body { font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto;"
        " max-width: 60rem; padding: 0 1rem; }\n"
        "  li { margin: .35rem 0; word-break: break-all; }\n"
        "  input:checked + a { opacity: .45; text-decoration: line-through; }\n"
        "</style>\n</head>\n<body>\n"
        f"<h1>{t}</h1>\n"
        f"<p>{len(urls)} pages to clip with the Obsidian Web Clipper. "
        "Each link opens in a new tab; tick it off once clipped. "
        "Clips land in <code>vault/Clippings/</code>.</p>\n"
        f"<ol>\n{rows}\n</ol>\n</body>\n</html>\n"
    )


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Hand browser-only URLs to the user for clipping.")
    p.add_argument("urls", nargs="*", help="URLs, or '-' to read one per line from stdin")
    p.add_argument("--from-json", default=None, help="JSON list of URLs or {url: ...} objects")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help=f"Max URLs handed over as tabs (default {DEFAULT_THRESHOLD})")
    p.add_argument("--out", default=None,
                   help="HTML path when over threshold (default .claude/tmp/clip-queue-<date>.html)")
    p.add_argument("--title", default=None, help="Heading for the HTML checklist")
    args = p.parse_args(argv)

    raw: list[str] = []
    for u in args.urls:
        if u == "-":
            raw.extend(sys.stdin.read().splitlines())
        else:
            raw.append(u)
    if args.from_json:
        try:
            data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
            raw.extend(_urls_from_json(data))
        except (OSError, ValueError) as e:
            print(f"error: cannot read --from-json {args.from_json}: {e}", file=sys.stderr)
            return 2

    urls = dedupe(raw)
    mode = decide(urls, args.threshold)
    html_path = None
    if mode == "html":
        today = date.today().isoformat()
        out = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"clip-queue-{today}.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(urls, args.title or f"Clip queue {today}"), encoding="utf-8")
        html_path = str(out)

    print(json.dumps({
        "mode": mode,
        "count": len(urls),
        "threshold": args.threshold,
        "urls": urls,
        "html_path": html_path,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
