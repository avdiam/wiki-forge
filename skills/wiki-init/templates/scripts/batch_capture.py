#!/usr/bin/env python3
"""Batch triage front-end for /wiki-capture.

`/wiki-capture` takes one URL at a time and its fetch+normalize step is
model-driven (WebFetch + cleaning the body). That part can't be scripted at
quality. What *can* be scripted is the deterministic front-half across a list:
dedup against the precomputed URL index, and domain-classify each URL so the
caller knows which capture path it takes.

This script does exactly that and nothing more — no network I/O. It reads a
text file of URLs (one per line; full-line `#` comments allowed) and sorts each
into one of four buckets:

  - already_in_vault    — URL is in vault/_indexes/url-index.json (dedup hit)
  - redirect_to_clipper — domain is on the auth_required list; only the
                          authenticated Chrome clipper can fetch it
  - fetchable           — head_preserving or default domain; the model captures
                          these via the normal /wiki-capture Step 3a cascade
  - errored             — line isn't a parseable http(s) URL

The `/wiki-capture --batch <file>` skill mode runs this with `--json`, presents
the buckets, then loops the model-driven capture over the `fetchable` bucket
(running the per-URL metadata cascade as usual) and chains the whole captured
batch into `/wiki-ingest`. The `redirect_to_clipper` bucket is handed to the
clipper; `already_in_vault` is skipped.

Classification is by domain only — no urllib fetch here. A `default`-domain URL
still needs a network fetch to decide use-metadata vs fallback-to-webfetch, but
that fetch happens in the model's per-URL cascade during the capture loop, so
doing it here would just double the network work. Keeping this leg network-free
makes it fast and deterministic even on hundred-URL lists.

Stdlib only — same discipline as extract_page_metadata.py (which it imports for
the classifier). No requests, no PyYAML.

Run:
    python wiki/.scripts/batch_capture.py <urls.txt> [--vault vault] [--json]

Exit codes:
  0 — ran (buckets printed); a non-empty `errored` bucket is not a failure
  2 — usage error (missing/unreadable input file)
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

# Windows-friendly: switch stdout/stderr to UTF-8 so the → / — characters in
# the human-readable report don't crash on cp1252/cp1253 consoles. Same guard
# as check_mirrors.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Import the classifier from the sibling script. Same .scripts/ directory, so
# put it on the path explicitly — batch_capture may be invoked from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_page_metadata as epm  # noqa: E402

INDEX_REL_PATH = "_indexes/url-index.json"


def parse_url_file(path: Path) -> list[str]:
    """Read URLs from a text file — one per line, full-line `#` comments skipped.

    Only full-line comments are honored (a line whose first non-whitespace char
    is `#`). Inline `# ...` trailing comments are NOT stripped, because URL
    fragments legitimately contain `#` (e.g. `example.com/p#section`). Blank
    lines are ignored. Each surviving line is returned verbatim (stripped of
    surrounding whitespace) — validation happens in classify_urls.
    """
    urls = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def _normalize_for_dedup(url: str) -> str:
    """Canonicalize a URL for dedup comparison only (original is preserved).

    Lowercases scheme + host, drops the fragment, strips a single trailing
    slash from the path. Conservative: query strings are kept (they can be
    load-bearing), so `?a=1` and `?a=2` are treated as distinct. Matches the
    light-touch shape the URL-based dedup in /wiki-capture Step 2 expects.
    """
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.port:
        host = f"{host}:{p.port}"
    path = p.path.rstrip("/")
    scheme = p.scheme.lower()
    canon = f"{scheme}://{host}{path}"
    if p.query:
        canon += "?" + p.query
    return canon


def load_index_urls(vault_root: Path) -> dict[str, str]:
    """Map normalized-URL → vault-relative path from the precomputed index.

    Returns an empty dict (with a stderr note) if the index is missing or
    unparseable — dedup degrades to "none known", which is safe: the caller
    just re-captures, and /wiki-ingest's content-hash dedup is the backstop.
    """
    index_path = vault_root / INDEX_REL_PATH
    if not index_path.is_file():
        print(
            f"note: URL index not found at {index_path} — dedup disabled this run",
            file=sys.stderr,
        )
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        print(f"note: URL index unparseable ({e}) — dedup disabled this run", file=sys.stderr)
        return {}
    out = {}
    for entry in data.get("entries", []):
        url = entry.get("url")
        if not url:
            continue
        out[_normalize_for_dedup(url)] = entry.get("filename", "?")
    return out


def classify_urls(urls: list[str], vault_root: Path) -> dict:
    """Sort URLs into the four buckets. No network I/O — domain match only."""
    index_urls = load_index_urls(vault_root)
    head_preserving, auth_required = epm._load_lists()

    buckets = {
        "fetchable": [],
        "redirect_to_clipper": [],
        "already_in_vault": [],
        "errored": [],
    }
    seen_in_batch: set[str] = set()

    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            buckets["errored"].append({"url": url, "reason": "not an http(s) URL"})
            continue

        canon = _normalize_for_dedup(url)

        # Intra-file duplicate — count once, flag the rest.
        if canon in seen_in_batch:
            buckets["errored"].append({"url": url, "reason": "duplicate within input file"})
            continue
        seen_in_batch.add(canon)

        if canon in index_urls:
            buckets["already_in_vault"].append({"url": url, "vault_path": index_urls[canon]})
            continue

        classification, host = epm.classify(url, head_preserving, auth_required)
        if classification == "auth_required":
            buckets["redirect_to_clipper"].append({"url": url, "domain": host})
        else:
            # head_preserving → trust metadata via WebFetch prompt.
            # default → model runs the metadata cascade (network) per-URL.
            next_action = (
                "use-webfetch-prompt"
                if classification == "head_preserving"
                else "run-metadata-cascade"
            )
            buckets["fetchable"].append(
                {"url": url, "domain": host, "classification": classification, "next_action": next_action}
            )

    return buckets


def build_report(input_file: str, urls: list[str], buckets: dict) -> dict:
    return {
        "input_file": input_file,
        "total": len(urls),
        "counts": {k: len(v) for k, v in buckets.items()},
        **buckets,
    }


def print_human(report: dict) -> None:
    counts = report["counts"]
    print(f"Batch triage — {report['input_file']}  ({report['total']} URLs)")
    print(
        f"  fetchable {counts['fetchable']}  |  "
        f"clipper {counts['redirect_to_clipper']}  |  "
        f"in-vault {counts['already_in_vault']}  |  "
        f"errored {counts['errored']}"
    )

    if report["fetchable"]:
        print("\nFETCHABLE (main session captures these):")
        for item in report["fetchable"]:
            print(f"  [{item['classification']}] {item['url']}")
    if report["redirect_to_clipper"]:
        print("\nREDIRECT TO CLIPPER (auth_required — needs the browser session):")
        for item in report["redirect_to_clipper"]:
            print(f"  {item['url']}")
    if report["already_in_vault"]:
        print("\nALREADY IN VAULT (skipped):")
        for item in report["already_in_vault"]:
            print(f"  {item['url']}  →  {item['vault_path']}")
    if report["errored"]:
        print("\nERRORED (not captured):")
        for item in report["errored"]:
            print(f"  {item['url']}  ({item['reason']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url_file", help="Text file of URLs, one per line (full-line # comments allowed)")
    parser.add_argument("--vault", default="vault", help="Path to vault root (default: vault)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of the human-readable report")
    args = parser.parse_args()

    url_path = Path(args.url_file)
    if not url_path.is_file():
        print(f"error: URL file not found: {url_path}", file=sys.stderr)
        return 2

    urls = parse_url_file(url_path)
    buckets = classify_urls(urls, Path(args.vault))
    report = build_report(args.url_file, urls, buckets)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
