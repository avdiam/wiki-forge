#!/usr/bin/env python3
"""Per-source URL polling — phase 2 of the source-refresh mitigation.

For each source page in `wiki/pages/sources/`, resolves the upstream URL
(indirectly via the page's `sources:` link → vault file's `url:`
frontmatter), HEAD-then-GETs it, extracts stable main content, hashes,
and compares against the slug's first-ever baseline. Drift → edit the
source page to add `source_drifted: true` frontmatter + a warning
callout, AND append a JSONL row. Clean polls just append a JSONL row.

Hash target is post-extraction text (script/style/nav/header/footer/
aside/form stripped, whitespace collapsed) — raw HTML hashes are useless
because most pages mutate on every load (timestamps, CSRF tokens, ad
slots, A/B variants).

Pure stdlib. The wiki's "no PyYAML, no requests, no trafilatura" property
is preserved — this script uses html.parser, hashlib, urllib.request only.

Usage:
    python check_source_freshness.py <wiki-root> [--stale-only] [--dry-run]
                                      [--limit N] [--slug <slug>]

`--stale-only` polls only sources whose `captured:` is older than 180
days. `--dry-run` skips JSONL writes and source-page edits but still
prints planned actions. `--slug <slug>` polls a single named slug.

Exit codes:
    0 — all polls completed (drift may have been recorded)
    1 — at least one poll failed at the network layer
    2 — fatal: not a wiki root, no pages/sources/ directory, etc.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


from frontmatter import parse_frontmatter  # noqa: E402

STALE_SOURCE_DAYS = 180
TIMEOUT_SECONDS = 10
MAX_RETRIES = 1
USER_AGENT = "wiki-freshness-check/1.0 (+https://github.com/avdia/wiki)"

# Domains that require clipper-side fetch (paywalled / login-gated).
# Skip with reason='requires_clipper'; phase 3 wires those to clipper.
PAYWALL_DOMAINS = {
    "medium.com", "towardsdatascience.com",
    "towardsai.net", "generativeai.pub", "ai.gopubby.com",
    "blog.gopenai.com", "ai.plainenglish.io",
    "levelup.gitconnected.com", "betterprogramming.pub",
}

STRIPPED_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "iframe"}


class TextExtractor(HTMLParser):
    """Strip tags in STRIPPED_TAGS and collect remaining visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in STRIPPED_TAGS:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in STRIPPED_TAGS and self._depth > 0:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth == 0 and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        joined = " ".join(self._chunks)
        return re.sub(r"\s+", " ", joined).strip()


def extract_text(html: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        # html.parser is forgiving but malformed input may still raise.
        return ""
    return parser.text()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def is_paywalled(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    host = host.lower()
    if host in PAYWALL_DOMAINS:
        return True
    parts = host.split(".")
    if len(parts) >= 2 and ".".join(parts[-2:]) in PAYWALL_DOMAINS:
        return True
    return False


def http_fetch(url: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Returns (http_status, body, error_class). On success: (status, body, None).
    On HTTP error: (status, None, None) — status is the upstream code.
    On network error: (None, None, error_class)."""
    last_error: Optional[str] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, body, None
        except urllib.error.HTTPError as e:
            return e.code, None, None
        except urllib.error.URLError as e:
            reason = str(e.reason).lower()
            if "timed out" in reason or "timeout" in reason:
                last_error = "timeout"
            elif "name or service" in reason or "nodename nor servname" in reason:
                last_error = "dns"
            else:
                last_error = "connection"
        except (OSError, ValueError) as e:
            last_error = type(e).__name__.lower()
        if attempt < MAX_RETRIES:
            time.sleep(1.0)
    return None, None, last_error


def latest_baseline(jsonl_path: Path, slug: str) -> Optional[str]:
    """Return the first-ever baseline_hash for `slug`, or None.

    The baseline is the `baseline_hash` recorded on the slug's first row
    in source_freshness.jsonl (where baseline_hash == content_hash and
    drifted == false). If no row exists yet, returns None — caller
    establishes the baseline on this poll.
    """
    if not jsonl_path.is_file():
        return None
    baseline: Optional[str] = None
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("slug") != slug:
                continue
            bh = row.get("baseline_hash")
            if isinstance(bh, str) and bh:
                baseline = bh
                break  # first row for this slug
    return baseline


def read_url_from_vault(vault_file: Path) -> Optional[str]:
    if not vault_file.is_file():
        return None
    try:
        text = vault_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = parse_frontmatter(text)
    url = fm.get("url")
    return url if isinstance(url, str) and url.startswith("http") else None


def days_since(yyyymmdd: str) -> Optional[int]:
    from datetime import date, datetime
    try:
        dt = datetime.strptime(yyyymmdd, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - dt).days


def edit_source_page_for_drift(source_page: Path) -> bool:
    """Add `source_drifted: true` frontmatter and inline callout. Idempotent.

    Injects the field INSIDE the existing YAML frontmatter block (before the
    closing fence) — not as a second `---...---` block, which would leave the
    field unreachable to YAML parsers (including `lint_wiki.py`'s
    `check_drifted_sources`). Bug surfaced 2026-05-14 in the Phase 3
    end-to-end synthetic drift test (session 65).
    """
    text = source_page.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm.get("source_drifted") is True:
        return False  # already marked
    if "source_drifted" in fm:
        # Key present with a non-True value (e.g., `false` from a manual edit
        # or future writer). Appending another `source_drifted: true` line
        # would produce a duplicate-key YAML, which different parsers resolve
        # differently. Skip; the page needs manual cleanup before re-marking.
        print(f"warning: {source_page.name} has source_drifted={fm.get('source_drifted')!r} "
              f"(non-True non-absent); skipping. Resolve manually before re-running.",
              file=sys.stderr)
        return False
    if not text.startswith("---"):
        return False
    end_marker = text.find("\n---", 3)
    if end_marker == -1:
        return False
    fm_head = text[:end_marker].rstrip()  # opening --- + fields, no trailing whitespace
    body = text[end_marker + 4:].lstrip("\n")  # post-frontmatter body, no leading blank lines
    new_fm = fm_head + "\nsource_drifted: true\n---"
    callout = ("> [!WARNING] Source drift detected\n"
               "> Upstream content has changed since this source was captured. "
               "Re-capture via clipper to refresh; the ingestor will rewrite "
               "this page from the new vault file.\n")
    if "Source drift detected" in body:
        # Idempotent re-mark: callout already present, just inject the frontmatter field.
        source_page.write_text(new_fm + "\n\n" + body, encoding="utf-8")
    else:
        source_page.write_text(new_fm + "\n\n" + callout + "\n" + body, encoding="utf-8")
    return True


def resolve_vault_path(wiki_root: Path, sources_field: list) -> Optional[Path]:
    """Return the first parseable vault path from a source page's `sources:` list."""
    if not sources_field:
        return None
    for entry in sources_field:
        if not isinstance(entry, str):
            continue
        if entry.startswith("vault/") or entry.startswith("vault\\"):
            return wiki_root.parent / Path(entry.replace("\\", "/"))
    return None


def append_freshness_row(wiki_root: Path, row: dict) -> None:
    meta_dir = wiki_root / ".meta"
    meta_dir.mkdir(exist_ok=True)
    log_path = meta_dir / "source_freshness.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def poll_one(
    wiki_root: Path,
    slug: str,
    source_page: Path,
    fm: dict,
    jsonl_path: Path,
    *,
    dry_run: bool,
) -> dict:
    """Poll one source. Returns the row appended (or that would be appended on dry-run)."""
    from datetime import datetime
    polled_at = datetime.now().isoformat(timespec="seconds")
    base_row = {
        "polled_at": polled_at,
        "slug": slug,
        "url": None,
        "http_status": None,
        "content_hash": None,
        "baseline_hash": None,
        "drifted": False,
        "error": None,
        "reason": None,
    }

    sources_list = fm.get("sources") or []
    if not isinstance(sources_list, list):
        sources_list = []
    vault_path = resolve_vault_path(wiki_root, sources_list)
    if vault_path is None:
        base_row["http_status"] = "skipped"
        base_row["reason"] = "no_vault_link"
        return base_row

    url = read_url_from_vault(vault_path)
    if url is None:
        base_row["http_status"] = "skipped"
        base_row["reason"] = "no_url"
        return base_row
    base_row["url"] = url

    if is_paywalled(url):
        base_row["http_status"] = "skipped"
        base_row["reason"] = "requires_clipper"
        return base_row

    status, body, err = http_fetch(url)
    if err is not None:
        base_row["http_status"] = "error"
        base_row["error"] = err
        return base_row
    base_row["http_status"] = status
    if status is None or status >= 400 or body is None:
        # Non-2xx: record status, no hash compare.
        return base_row

    content = extract_text(body)
    if not content:
        base_row["error"] = "empty_extraction"
        return base_row
    content_hash = hash_text(content)
    base_row["content_hash"] = content_hash

    baseline = latest_baseline(jsonl_path, slug)
    if baseline is None:
        # Establish baseline on first successful poll.
        base_row["baseline_hash"] = content_hash
        base_row["drifted"] = False
    else:
        base_row["baseline_hash"] = baseline
        base_row["drifted"] = (content_hash != baseline)
        if base_row["drifted"] and not dry_run:
            edit_source_page_for_drift(source_page)

    return base_row


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Poll source URLs for content drift.")
    p.add_argument("wiki", help="Path to the wiki root")
    p.add_argument("--stale-only", action="store_true",
                   help=f"Poll only sources whose captured: is older than {STALE_SOURCE_DAYS} days")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write JSONL or edit source pages — print planned actions only")
    p.add_argument("--limit", type=int, default=0, help="Stop after N polls (0 = no limit)")
    p.add_argument("--slug", default=None, help="Poll a single slug only")
    args = p.parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root", file=sys.stderr)
        return 2
    sources_dir = wiki_root / "pages" / "sources"
    if not sources_dir.is_dir():
        print(f"error: {sources_dir} does not exist", file=sys.stderr)
        return 2
    jsonl_path = wiki_root / ".meta" / "source_freshness.jsonl"

    candidates: list[tuple[str, Path, dict]] = []
    for md in sorted(sources_dir.glob("*.md")):
        slug = md.stem
        if args.slug and slug != args.slug:
            continue
        text = md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if args.stale_only:
            captured = fm.get("captured")
            if not isinstance(captured, str):
                continue
            age = days_since(captured)
            if age is None or age <= STALE_SOURCE_DAYS:
                continue
        candidates.append((slug, md, fm))
        if args.limit and len(candidates) >= args.limit:
            break

    if not candidates:
        print("no sources to poll")
        return 0

    network_failures = 0
    for slug, source_page, fm in candidates:
        # Capture baseline-existence BEFORE poll_one runs, so we can label
        # the first-successful-poll case as "baseline" rather than "ok".
        had_baseline = latest_baseline(jsonl_path, slug) is not None
        row = poll_one(wiki_root, slug, source_page, fm, jsonl_path, dry_run=args.dry_run)
        if not args.dry_run:
            append_freshness_row(wiki_root, row)
        # Compact human line per slug — easier to scan than the JSONL.
        status = row["http_status"]
        if row["error"]:
            tag = f"error:{row['error']}"
            network_failures += 1
        elif row["reason"]:
            tag = f"skip:{row['reason']}"
        elif row["drifted"]:
            tag = "DRIFTED"
        elif row["content_hash"]:
            tag = "ok" if had_baseline else "baseline"
        else:
            tag = f"http:{status}"
        print(f"{slug}: {tag}")
    print(f"\npolled {len(candidates)} sources, {network_failures} network failures")
    return 1 if network_failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
