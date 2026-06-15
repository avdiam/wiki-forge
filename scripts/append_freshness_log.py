#!/usr/bin/env python3
"""Append one row to wiki/.meta/source_freshness.jsonl.

Called by wiki/.scripts/check_source_freshness.py for each source-page
slug it polls. Each row records one HTTP poll attempt.

Row schema:
    {
      "polled_at": "2026-05-07T14:35:12",
      "slug": "2025-11-25-foo-bar",
      "url": "https://example.com/foo",
      "http_status": 200,
      "content_hash": "a1b2c3d4e5f6a7b8",
      "baseline_hash": "a1b2c3d4e5f6a7b8",
      "drifted": false,
      "error": null,
      "reason": null
    }

`http_status` is the upstream HTTP code on a successful fetch (200,
404, ...) OR a string sentinel for skipped / failed polls:
  - `"skipped"` with `reason: "requires_clipper"` — paywall domain
    (Medium and known mirrors); phase 3 wires these to clipper.
  - `"skipped"` with `reason: "no_url"` or `"no_vault_link"` — source
    page has no resolvable upstream URL; nothing to poll.
  - `"error"` with `error: "timeout" | "dns" | "connection" | ...` —
    network failure after 1 retry; row is recorded so the failure is
    audit-visible.

`content_hash` is `sha256(extracted_text)[:16]` — same 16-hex shape as
`source_hash:` for visual parity but hashing different bytes (extracted
main content via stdlib `html.parser`, with `<script>`, `<style>`,
`<nav>`, `<header>`, `<footer>`, `<aside>`, `<form>`, `<noscript>`,
`<iframe>` stripped and whitespace collapsed). Raw-HTML hashes are
useless because most pages mutate every load (timestamps, CSRF tokens,
ad slots, A/B variants); extraction is what makes drift detection
signal rather than noise.

`baseline_hash` is the slug's first-ever `content_hash`. First
successful poll establishes baseline (`baseline_hash == content_hash`,
`drifted: false`). Every subsequent poll compares against that first
baseline; mismatch sets `drifted: true` and the polling script edits
the source page to add `source_drifted: true` frontmatter plus an
inline `> [!WARNING] Source drift detected` callout. The
`check_drifted_sources` lint check surfaces those flagged pages.
Drift-clear path: re-capture via clipper, ingestor regenerates the
source page from the fresh vault file, drift flag and callout
disappear naturally.

Pass `""` or `"null"` for any optional field that doesn't apply.

Usage:
    python append_freshness_log.py --wiki <wiki-root> \\
        --slug 2026-04-15-foo --url "https://example.com/foo" \\
        --http-status 200 --content-hash a1b2c3d4e5f6a1b2 \\
        --baseline-hash a1b2c3d4e5f6a1b2 --drifted false
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from _log_writer import append_jsonl, optional_str, resolve_wiki_root, strict_bool


def http_status_value(raw: str) -> "int | str | None":
    """Accept integer codes (200, 404) OR string sentinels ('skipped', 'error')."""
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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

    wiki_root = resolve_wiki_root(args.wiki)
    if wiki_root is None:
        return 2

    log_path = wiki_root / ".meta" / "source_freshness.jsonl"
    append_jsonl(log_path, build_row(args))

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
