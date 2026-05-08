#!/usr/bin/env python3
"""
URL → page-metadata extractor for /wiki-capture.

Cascade:
  1. Classify URL by domain against url_classifier_lists.json:
       - auth_required: skip fetch, redirect to clipper.
       - head_preserving: WebFetch path (Path 1) — caller invokes WebFetch with
         the metadata-targeted prompt; this script returns no metadata of its
         own for these.
       - default: try urllib fetch (Path 3); on success extract og: /
         JSON-LD keywords / article:tag / citation_*. On fetch failure, signal
         caller to fall through to WebFetch with verification gate.
  2. Output a single JSON object to stdout — see the `result` shape below.

Stdlib only (urllib + html.parser + json) — same discipline as
check_source_freshness.py. No requests, no PyYAML, no trafilatura.
"""

import gzip
import io
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT_SECONDS = 10


def _load_lists():
    """Load url_classifier_lists.json from this script's directory."""
    p = Path(__file__).parent / "url_classifier_lists.json"
    with p.open() as f:
        data = json.load(f)
    return data.get("head_preserving", []), data.get("auth_required", [])


def _domain_matches(host, entry):
    return host == entry or host.endswith("." + entry)


def classify(url, head_preserving, auth_required):
    """Return ('auth_required'|'head_preserving'|'default', host)."""
    host = (urlparse(url).hostname or "").lower()
    for entry in auth_required:
        if _domain_matches(host, entry):
            return "auth_required", host
    for entry in head_preserving:
        if _domain_matches(host, entry):
            return "head_preserving", host
    return "default", host


class _MetaExtractor(HTMLParser):
    """Collect <meta> and <script type="application/ld+json"> from the whole document.

    Why not <head>-only: Next.js / React SSR commonly emits OG metadata outside
    <head> (anthropic.com is a working example — head closes at byte ~4.7K, og:title
    appears at byte ~48K). The HTML standard places metadata in <head> but browsers
    accept it anywhere, and modern frameworks rely on that. Restricting to <head>
    silently drops valid metadata on a meaningful share of public-web sites.
    """

    def __init__(self):
        super().__init__()
        self.in_jsonld = False
        self._jsonld_buf = []
        self.metas = []
        self.jsonld_blobs = []

    def handle_starttag(self, tag, attrs):
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            self.metas.append(attrs_d)
        elif tag == "script":
            t = attrs_d.get("type", "").lower()
            if t == "application/ld+json":
                self.in_jsonld = True
                self._jsonld_buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self.in_jsonld:
            self.jsonld_blobs.append("".join(self._jsonld_buf))
            self.in_jsonld = False
            self._jsonld_buf = []

    def handle_data(self, data):
        if self.in_jsonld:
            self._jsonld_buf.append(data)


def _decode_body(raw_bytes, content_encoding):
    if content_encoding and content_encoding.lower() == "gzip":
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except OSError:
            pass
    return raw_bytes.decode("utf-8", errors="replace")


def _extract_jsonld_top_level(blob):
    """Top-level only. Pull keywords / about / articleSection from a JSON-LD blob.
    Returns a list of strings. Skips silently on parse error."""
    out = []
    try:
        obj = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return out
    candidates = obj if isinstance(obj, list) else [obj]
    for c in candidates:
        if not isinstance(c, dict):
            continue
        for key in ("keywords", "about", "articleSection"):
            v = c.get(key)
            if v is None:
                continue
            if isinstance(v, str):
                out.extend([s.strip() for s in v.split(",") if s.strip()])
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        out.append(item.strip())
                    elif isinstance(item, dict):
                        name = item.get("name")
                        if isinstance(name, str):
                            out.append(name.strip())
    return out


def fetch_and_extract(url):
    """Path 3: fetch via urllib + parse <head>. Returns (status_dict, metadata_dict)."""
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, identity"})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            body_bytes = r.read()
            ce = r.headers.get("Content-Encoding", "")
            html = _decode_body(body_bytes, ce)
            status = {"fetch_status": "ok", "http_code": r.status}
    except HTTPError as e:
        return {"fetch_status": "blocked", "http_code": e.code, "fetch_error": f"HTTP {e.code}"}, _empty_metadata()
    except URLError as e:
        return {"fetch_status": "error", "fetch_error": f"URLError: {e.reason}"}, _empty_metadata()
    except (TimeoutError, OSError) as e:
        return {"fetch_status": "error", "fetch_error": f"{type(e).__name__}: {e}"}, _empty_metadata()

    parser = _MetaExtractor()
    try:
        parser.feed(html)
    except Exception as e:  # html.parser is permissive; this is defensive only
        status["fetch_error"] = f"parse: {type(e).__name__}: {e}"

    og = {}
    article_tags = []
    citation_metas = {}
    for m in parser.metas:
        prop = m.get("property", "")
        name = m.get("name", "")
        content = m.get("content", "")
        if not content:
            continue
        if prop.startswith("og:"):
            og[prop] = content
        if prop == "article:tag" or name == "article:tag":
            article_tags.append(content)
        if name.startswith("citation_"):
            # citation_author can repeat — collect all values into a list
            existing = citation_metas.get(name)
            if existing is None:
                citation_metas[name] = content
            elif isinstance(existing, list):
                existing.append(content)
            else:
                citation_metas[name] = [existing, content]

    json_ld_keywords = []
    for blob in parser.jsonld_blobs:
        json_ld_keywords.extend(_extract_jsonld_top_level(blob))

    metadata = {
        "og": og,
        "json_ld_keywords": json_ld_keywords,
        "article_tags": article_tags,
        "citation_metas": citation_metas,
    }
    return status, metadata


def _empty_metadata():
    return {"og": {}, "json_ld_keywords": [], "article_tags": [], "citation_metas": {}}


def _has_any_metadata(md):
    return bool(md["og"] or md["json_ld_keywords"] or md["article_tags"] or md["citation_metas"])


def _decide_tag_source(classification, metadata, fetch_status):
    if classification == "auth_required":
        return "clippings-marker"
    if classification == "head_preserving":
        return "needs-verification"  # caller will fill via WebFetch + user gate
    if fetch_status == "ok" and metadata["citation_metas"]:
        return "arxiv-citation"
    if fetch_status == "ok" and _has_any_metadata(metadata):
        return "page-meta"
    return "clippings-marker"


def _decide_next_action(classification, fetch_status, has_metadata):
    if classification == "auth_required":
        return "skip-redirect-to-clipper"
    if classification == "head_preserving":
        return "use-webfetch-prompt"
    # default — Path 3 outcome
    if fetch_status == "ok":
        return "use-metadata-below"
    return "fallback-to-webfetch-with-verification"


def main():
    if len(sys.argv) != 2:
        print("Usage: extract_page_metadata.py <url>", file=sys.stderr)
        sys.exit(2)
    url = sys.argv[1]
    head_preserving, auth_required = _load_lists()
    classification, host = classify(url, head_preserving, auth_required)

    if classification == "auth_required":
        result = {
            "url": url,
            "domain": host,
            "classification": classification,
            "next_action": "skip-redirect-to-clipper",
            "fetch_status": "skipped",
            "metadata": _empty_metadata(),
            "tag_source": "clippings-marker",
        }
        print(json.dumps(result, indent=2))
        return

    if classification == "head_preserving":
        result = {
            "url": url,
            "domain": host,
            "classification": classification,
            "next_action": "use-webfetch-prompt",
            "fetch_status": "skipped",
            "metadata": _empty_metadata(),
            "tag_source": "needs-verification",
        }
        print(json.dumps(result, indent=2))
        return

    # default — try Path 3
    status, metadata = fetch_and_extract(url)
    fetch_status = status["fetch_status"]
    next_action = _decide_next_action(classification, fetch_status, _has_any_metadata(metadata))
    tag_source = _decide_tag_source(classification, metadata, fetch_status)

    result = {
        "url": url,
        "domain": host,
        "classification": classification,
        "next_action": next_action,
        "fetch_status": fetch_status,
        "metadata": metadata,
        "tag_source": tag_source,
    }
    if "http_code" in status:
        result["http_code"] = status["http_code"]
    if "fetch_error" in status:
        result["fetch_error"] = status["fetch_error"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
