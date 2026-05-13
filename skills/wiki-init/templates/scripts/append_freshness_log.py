#!/usr/bin/env python3
"""Append one row to wiki/.meta/source_freshness.jsonl.

Called by wiki/.scripts/check_source_freshness.py for each source-page
slug it polls. Schema-symmetric with the other .meta/*_log.jsonl
appenders. Schema: see wiki/SPECIFICATION.md § Telemetry, or run --help.

Each row records one HTTP poll attempt:

  {slug, polled_at, url, http_status, content_hash, drifted,
   baseline_hash, error, reason}

Pass `""` or `"null"` for any optional field that doesn't apply.

Usage:
    python append_freshness_log.py --wiki <wiki-root> \\
        --slug 2026-04-15-foo --url "https://example.com/foo" \\
        --http-status 200 --content-hash a1b2c3d4e5f6a1b2 \\
        --baseline-hash a1b2c3d4e5f6a1b2 --drifted false
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def optional_str(raw: str) -> str | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return raw


def http_status_value(raw: str) -> "int | str | None":
    """Accept integer codes (200, 404) OR string sentinels ('skipped', 'error')."""
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def strict_bool(raw: str) -> bool:
    low = raw.lower()
    if low in ("true", "1", "yes"):
        return True
    if low in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got '{raw}'")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append a row to .meta/source_freshness.jsonl")
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    p.add_argument("--slug", required=True, help="Source page slug (filename without .md)")
    p.add_argument("--url", type=optional_str, default=None, help="URL polled (or null)")
    p.add_argument("--http-status", type=http_status_value, default=None,
                   help="HTTP status code (int) or sentinel ('skipped', 'error')")
    p.add_argument("--content-hash", type=optional_str, default=None,
                   help="16-hex sha256 prefix of extracted main content; null on skip/error")
    p.add_argument("--baseline-hash", type=optional_str, default=None,
                   help="The slug's first-ever content_hash; null on first poll where this is establishing baseline")
    p.add_argument("--drifted", type=strict_bool, default=False,
                   help="True iff content_hash != baseline_hash")
    p.add_argument("--error", type=optional_str, default=None,
                   help="Error class on http_status='error' rows: 'timeout', 'dns', 'connection', etc.")
    p.add_argument("--reason", type=optional_str, default=None,
                   help="Reason on http_status='skipped' rows: 'requires_clipper', 'no_url', etc.")
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "polled_at": datetime.now().isoformat(timespec="seconds"),
        "slug": args.slug,
        "url": args.url,
        "http_status": args.http_status,
        "content_hash": args.content_hash,
        "baseline_hash": args.baseline_hash,
        "drifted": args.drifted,
        "error": args.error,
        "reason": args.reason,
    }


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    meta_dir = wiki_root / ".meta"
    meta_dir.mkdir(exist_ok=True)
    log_path = meta_dir / "source_freshness.jsonl"

    row = build_row(args)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
