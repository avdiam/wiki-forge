#!/usr/bin/env python3
"""Mechanical lint checks for the wiki.

Fast deterministic checks only. Semantic checks (unacknowledged
contradictions, category drift, stale facts) are left to the wiki-linter
subagent, which reads flagged pages with LLM reasoning.

Checks:
    - orphans                 pages with no inbound [[wikilinks]]
    - broken_links            wikilinks pointing at pages that don't exist
    - missing_frontmatter     pages missing required fields
    - stale                   stale:true OR last_updated > 90 days ago
    - low_confidence          confidence < 0.5
    - index_drift             page dirs vs indexes/index-*.md mismatch
    - source_backlog          vault/ files with no matching sources/ page
    - broken_sources          sources: frontmatter entries pointing to missing vault files
    - overview_staleness      overview.md refresh marker lags log.md ingest count by >=5
    - single_page_categories  categories with exactly one page (folds should be flat)
    - long_summary            summary: fields >30 words or multi-sentence
    - stale_source            source pages whose captured: is older than 180 days
    - drifted_sources         source pages flagged source_drifted:true by check_source_freshness.py
    - asymmetric_contradictions  A contradicts B but B doesn't list A
    - invalid_typed_links     wikilinks in contradicts:/supersedes:/superseded_by: that don't resolve
    - artifact_presence       opted-in pages lacking any structural artifact (code/table/procedure/quote/deflist)
    - unresolved_link_hubs    slugs with >=10 incoming wikilinks but no page (stub candidates)
    - drifted_stubs           pages with stub-prose markers but no verify:true (shape drift)

Usage:
    python lint_wiki.py <wiki-root> [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from constants import PAGE_TYPES
from frontmatter import parse_frontmatter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


REQUIRED_FIELDS = ["title", "type", "category", "summary", "confidence", "sources", "last_updated"]
STALE_DAYS = 90
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
AMBIGUOUS_MARKER_RE = re.compile(r"\*\[AMBIGUOUS\]\*")
CLAIM_TAG_SCOPE_TYPES = {"concepts", "entities", "comparisons", "syntheses"}


def collect_pages(wiki_root: Path) -> dict:
    """Collect all pages from type directories directly under wiki root."""
    pages: dict = {}
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            pages[md.stem] = {
                "path": md.relative_to(wiki_root).as_posix(),
                "type": page_type,
                "frontmatter": parse_frontmatter(text),
                "body": text,
            }
    return pages


def find_wikilinks(text: str) -> list[str]:
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]


def check_orphans(pages: dict) -> list[str]:
    referenced: set[str] = set()
    for info in pages.values():
        for link in find_wikilinks(info["body"]):
            referenced.add(link)
    return sorted(slug for slug in pages if slug not in referenced)


def check_broken_links(pages: dict) -> list[dict]:
    broken: list[dict] = []
    slugs = set(pages.keys())
    for slug, info in pages.items():
        for link in find_wikilinks(info["body"]):
            if link not in slugs:
                broken.append({"page": info["path"], "target": link})
    return broken


SOURCELESS_STUB_ORIGINS = {"hub", "planned"}


def check_missing_frontmatter(pages: dict) -> list[dict]:
    issues: list[dict] = []
    for slug, info in pages.items():
        fm = info["frontmatter"]
        missing = [f for f in REQUIRED_FIELDS if not fm.get(f) and fm.get(f) != 0]
        # Carve-out: hub and planned stubs are defined by `sources: []`. The
        # `stub_origin:` field + `verify: true` carry the metadata that would
        # normally come from a source — don't double-flag those.
        if "sources" in missing and fm.get("stub_origin") in SOURCELESS_STUB_ORIGINS:
            missing = [f for f in missing if f != "sources"]
        if missing:
            issues.append({"page": info["path"], "missing": missing})
    return issues


def check_stale(pages: dict) -> list[dict]:
    issues: list[dict] = []
    cutoff = date.today() - timedelta(days=STALE_DAYS)
    for slug, info in pages.items():
        fm = info["frontmatter"]
        if fm.get("stale") is True:
            issues.append({"page": info["path"], "reason": "stale: true in frontmatter"})
            continue
        last = fm.get("last_updated")
        if isinstance(last, str):
            try:
                dt = datetime.strptime(last, "%Y-%m-%d").date()
                if dt < cutoff:
                    issues.append({"page": info["path"], "reason": f"last_updated {last} (> {STALE_DAYS} days ago)"})
            except ValueError:
                pass
    return issues


def check_low_confidence(pages: dict) -> list[dict]:
    issues: list[dict] = []
    for slug, info in pages.items():
        conf = info["frontmatter"].get("confidence")
        if isinstance(conf, (int, float)):
            if conf < 0.0 or conf > 1.0:
                issues.append({"page": info["path"], "confidence": conf, "reason": "out of range (must be 0.0-1.0)"})
            elif conf < 0.5:
                issues.append({"page": info["path"], "confidence": conf})
    return issues


def check_index_drift(wiki_root: Path, pages: dict) -> dict:
    """Check for drift between page dirs and indexes/index-*.md files."""
    indexes_dir = wiki_root / "indexes"
    index_text = ""
    found_indices = False
    if not indexes_dir.is_dir():
        return {"missing_index": True, "in_pages_not_index": [], "in_index_not_pages": []}
    for idx_file in sorted(indexes_dir.glob("index-*.md")):
        found_indices = True
        try:
            index_text += idx_file.read_text(encoding="utf-8") + "\n"
        except OSError:
            continue

    if not found_indices:
        return {"missing_index": True, "in_pages_not_index": [], "in_index_not_pages": []}

    index_slugs = set(find_wikilinks(index_text))
    page_slugs = set(pages.keys())
    return {
        "missing_index": False,
        "in_pages_not_index": sorted(page_slugs - index_slugs),
        "in_index_not_pages": sorted(index_slugs - page_slugs),
    }


def _is_excluded_vault_path(relative_parts: tuple[str, ...]) -> bool:
    """Match the ingest-exclusion rule: top-level segment under vault/ begins with . or _.

    Catches protocol/config files (.wiki-identity.md, .clipper-rules.md),
    protocol folders (_mailbox/, .skipped/), and Obsidian internals (.obsidian/).
    """
    if not relative_parts:
        return False
    top = relative_parts[0]
    return top.startswith(".") or top.startswith("_")


def check_source_backlog(vault_dir: Path, pages: dict) -> list[str]:
    """Check for raw files anywhere under vault/ that have no corresponding source summary
    AND no skip stub.

    Recurses into subdirectories (e.g., vault/Web Articles/), and excludes any
    path whose top-level segment begins with . or _ (matches the ingest-exclusion
    rule in vault/.wiki-identity.md). A file is "handled" if it either has a
    source page in wiki/sources/ OR a stub in vault/.skipped/ — both are
    decisions recorded by the ingest flow; neither should re-surface as pending.
    """
    if not vault_dir.is_dir():
        return []
    source_slugs = {
        slug for slug, info in pages.items() if info["type"] == "sources"
    }
    skipped_dir = vault_dir / ".skipped"
    skipped_slugs: set[str] = set()
    if skipped_dir.is_dir():
        skipped_slugs = {p.stem for p in skipped_dir.glob("*.md")}
    handled = source_slugs | skipped_slugs
    backlog: list[str] = []
    for raw_file in sorted(vault_dir.rglob("*.md")):
        relative = raw_file.relative_to(vault_dir)
        if _is_excluded_vault_path(relative.parts):
            continue
        if raw_file.stem not in handled:
            backlog.append(relative.as_posix())
    return backlog


def check_broken_sources(vault_dir: Path, pages: dict) -> list[dict]:
    """Check that sources: frontmatter entries point to existing vault/ files.

    Frontmatter convention is full path from project root, e.g.
    'vault/Web Articles/2026-03-18-foo.md'. Resolve against vault_dir.parent.
    Fallback: if no 'vault/' prefix, treat as bare filename and search
    recursively in vault/.
    """
    issues: list[dict] = []
    for slug, info in pages.items():
        fm = info["frontmatter"]
        sources = fm.get("sources") or []
        if not isinstance(sources, list):
            continue
        for src in sources:
            if not isinstance(src, str):
                continue
            if src.startswith("vault/") or src.startswith("vault\\"):
                src_path = vault_dir.parent / Path(src.replace("\\", "/"))
            else:
                candidates = list(vault_dir.rglob(Path(src).name))
                src_path = candidates[0] if candidates else vault_dir / Path(src).name
            if not src_path.is_file():
                issues.append({"page": info["path"], "missing_source": src})
    return issues


def check_missing_source_type(pages: dict) -> list[str]:
    """Source pages lacking a source_type field (required for sources/ only)."""
    issues: list[str] = []
    for slug, info in pages.items():
        if info["type"] != "sources":
            continue
        if not info["frontmatter"].get("source_type"):
            issues.append(info["path"])
    return sorted(issues)


def check_needs_verification(pages: dict) -> list[str]:
    """Pages flagged `verify: true` — review queue for claims needing corroboration."""
    return sorted(
        info["path"] for info in pages.values()
        if info["frontmatter"].get("verify") is True
    )


def check_excessive_ambiguous(pages: dict, threshold: int = 3) -> list[str]:
    """Pages with more than `threshold` *[AMBIGUOUS]* markers in body.

    Concept / entity / comparison / synthesis pages only — source pages
    are exempt by convention (source fidelity carried by source_type).
    Per wiki/AGENTS.md "claim-tag inline markers" rule.
    """
    flagged = []
    for info in pages.values():
        if info["type"] not in CLAIM_TAG_SCOPE_TYPES:
            continue
        if len(AMBIGUOUS_MARKER_RE.findall(info["body"])) > threshold:
            flagged.append(info["path"])
    return sorted(flagged)


def check_drifted_sources(pages: dict) -> list[str]:
    """Source pages flagged `source_drifted: true` by check_source_freshness.py.

    The polling script edits the source page to add this flag (and an
    inline `> [!WARNING] Source drift detected` callout) when the
    upstream URL's extracted main content no longer matches the slug's
    first-ever baseline_hash. Surfacing the flag at lint time gives the
    user a review queue: re-capture via clipper to refresh, or
    investigate whether the drift is material.

    Phase-2 mitigation for [[llm-wiki-failure-modes]] #8 (dynamic-data
    mismatch). `check_stale_source` flags age; this flags actual drift.
    """
    return sorted(
        info["path"] for info in pages.values()
        if info["type"] == "sources"
        and info["frontmatter"].get("source_drifted") is True
    )


STALE_SOURCE_DAYS = 180


def check_stale_source(pages: dict) -> list[dict]:
    """Source pages whose `captured:` is older than 180 days.

    Surfaces a review queue for upstream content that may have drifted since
    capture — addresses [[llm-wiki-failure-modes]] #8 (dynamic-data mismatch).
    Flags source pages only; `captured:` is not expected on other page types.
    """
    issues: list[dict] = []
    cutoff = date.today() - timedelta(days=STALE_SOURCE_DAYS)
    for slug, info in pages.items():
        if info["type"] != "sources":
            continue
        captured = info["frontmatter"].get("captured")
        if not isinstance(captured, str):
            issues.append({"page": info["path"], "reason": "captured: missing"})
            continue
        try:
            dt = datetime.strptime(captured, "%Y-%m-%d").date()
        except ValueError:
            issues.append({"page": info["path"], "reason": f"captured: '{captured}' unparseable"})
            continue
        if dt < cutoff:
            age = (date.today() - dt).days
            issues.append({"page": info["path"], "reason": f"captured {captured} ({age} days old, >{STALE_SOURCE_DAYS})"})
    return issues


TYPED_EDGE_FIELDS = ("contradicts", "supersedes", "superseded_by")
TYPED_LINK_RE = re.compile(r"^\[\[([^\]|#]+?)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]$")


def _extract_typed_targets(field_value) -> list[str]:
    """Extract slugs from a typed-edge frontmatter value.

    Canonical shape is a YAML quoted-flow list: ["[[slug-a]]", "[[slug-b]]"].
    Tolerates a single string for forgiving authoring. Entries that don't
    parse as wikilinks are silently dropped here — `check_invalid_typed_links`
    surfaces those by comparing against the page set, so we don't double-flag.
    """
    if not field_value:
        return []
    if isinstance(field_value, str):
        candidates = [field_value]
    elif isinstance(field_value, list):
        candidates = [v for v in field_value if isinstance(v, str)]
    else:
        return []
    targets: list[str] = []
    for c in candidates:
        m = TYPED_LINK_RE.match(c.strip())
        if m:
            targets.append(m.group(1).strip())
    return targets


def check_asymmetric_contradictions(pages: dict) -> list[dict]:
    """For each page A listing `contradicts: [B, ...]`, verify B lists A back.

    The `contradicts:` relationship is symmetric: A contradicting B implies B
    contradicts A. One-way edges are typically a backfill miss or a typo.
    """
    issues: list[dict] = []
    for slug, info in pages.items():
        targets = _extract_typed_targets(info["frontmatter"].get("contradicts"))
        for target in targets:
            target_info = pages.get(target)
            if target_info is None:
                # broken target is reported by check_invalid_typed_links
                continue
            inverse = _extract_typed_targets(target_info["frontmatter"].get("contradicts"))
            if slug not in inverse:
                issues.append({
                    "page": info["path"],
                    "missing_inverse": target,
                })
    return issues


def check_invalid_typed_links(pages: dict) -> list[dict]:
    """Wikilinks in contradicts:/supersedes:/superseded_by: must resolve.

    Mirrors `check_broken_links` for the frontmatter typed-edge fields. The
    body-wikilink check doesn't read frontmatter, so without this a typo'd
    `contradicts: ["[[gone-slug]]"]` would slip through.
    """
    issues: list[dict] = []
    slugs = set(pages.keys())
    for slug, info in pages.items():
        for field in TYPED_EDGE_FIELDS:
            for target in _extract_typed_targets(info["frontmatter"].get(field)):
                if target not in slugs:
                    issues.append({
                        "page": info["path"],
                        "field": field,
                        "target": target,
                    })
    return issues


OVERVIEW_MARKER_RE = re.compile(r"Mechanical refresh at ingest #(\d+)", re.IGNORECASE)
OVERVIEW_STALE_THRESHOLD = 5


def check_overview_staleness(wiki_root: Path) -> dict | None:
    """Compare overview.md's refresh marker against the ingest count in log.md.

    Returns a dict when the overview is >=5 ingests behind, or the marker is
    missing entirely. Returns None when fresh.
    """
    overview_path = wiki_root / "overview.md"
    log_path = wiki_root / "log.md"
    if not overview_path.is_file() or not log_path.is_file():
        return None

    try:
        overview_text = overview_path.read_text(encoding="utf-8", errors="ignore")
        log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    match = OVERVIEW_MARKER_RE.search(overview_text)
    ingest_count = sum(1 for line in log_text.splitlines() if re.match(r"^## \[.*?\] ingest \|", line))

    if not match:
        if ingest_count == 0:
            return None
        return {
            "marker_ingest": None,
            "actual_ingests": ingest_count,
            "lag": ingest_count,
            "reason": "no refresh marker found in overview.md",
        }

    marker_n = int(match.group(1))
    lag = ingest_count - marker_n
    if lag >= OVERVIEW_STALE_THRESHOLD:
        return {
            "marker_ingest": marker_n,
            "actual_ingests": ingest_count,
            "lag": lag,
            "reason": f"overview.md last refreshed at ingest #{marker_n}; log.md shows {ingest_count} ingests ({lag} behind)",
        }
    return None


def check_single_page_categories(pages: dict) -> list[dict]:
    """Flag categories with exactly one page — per wiki/CATEGORIES.md 'never create a category with a single page'."""
    from collections import defaultdict
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for info in pages.values():
        category = info["frontmatter"].get("category") or "uncategorized"
        buckets[(info["type"], category)].append(info["path"])
    issues: list[dict] = []
    for (page_type, category), paths in sorted(buckets.items()):
        if len(paths) == 1 and category != "uncategorized":
            issues.append({
                "page_type": page_type,
                "category": category,
                "page": paths[0],
            })
    return issues


# Authoritative category list — loaded at runtime from wiki/CATEGORIES.md.
# Single source of truth as of the 2026-05-13 schema decomposition; the
# previous hardcoded frozenset was a manual-sync contract with drift risk
# (see dev/wiki-agents.md-refactor.md § Finding 1).
def load_authoritative_categories(wiki_root: Path) -> frozenset[str]:
    """Parse category slugs from wiki/CATEGORIES.md's enumerated list.

    Looks for the H2 header `## Authoritative category list` and extracts
    each bullet's slug — the text between the first backtick pair on lines
    matching `- `<slug>` — <description>`. The list terminates at the next
    `#`-prefixed header.
    """
    categories_path = wiki_root / "CATEGORIES.md"
    if not categories_path.is_file():
        print(
            f"error: {categories_path} missing — required since the "
            f"2026-05-13 schema decomposition. See dev/wiki-agents.md-refactor.md.",
            file=sys.stderr,
        )
        sys.exit(2)
    text = categories_path.read_text(encoding="utf-8")
    in_list = False
    cats: set[str] = set()
    for line in text.splitlines():
        if line.startswith("## Authoritative category list"):
            in_list = True
            continue
        if in_list and line.startswith("#"):
            break
        if in_list and line.lstrip().startswith("- `"):
            try:
                slug = line.split("`", 2)[1]
                if slug:
                    cats.add(slug)
            except IndexError:
                continue
    if not cats:
        print(f"error: no categories parsed from {categories_path}", file=sys.stderr)
        sys.exit(2)
    return frozenset(cats)


def check_single_source_unverified(pages: dict) -> list[dict]:
    """Flag concept/entity/comparison/synthesis pages with one source, confidence > 0.6, and no verify flag.

    Per wiki/AGENTS.md "Single-source standalone pages" rule: a new page with
    exactly one source must satisfy one of (confidence ≤ 0.6, verify: true,
    explicit user pre-approval at the takeaway gate). The first two are
    detectable in frontmatter; the third is undetectable from the page alone,
    so this check produces a soft-warning review queue rather than a hard
    error. Existing pages predating the rule are part of the queue and can
    be cleaned up incrementally.

    Source-summary pages are excluded — they're the raw-citation transit
    layer, not synthesized claims.
    """
    issues: list[dict] = []
    for info in pages.values():
        if info["type"] == "sources":
            continue
        sources = info["frontmatter"].get("sources")
        if not isinstance(sources, list):
            continue
        # Filter empty/whitespace entries
        src_count = len([s for s in sources if isinstance(s, str) and s.strip()])
        if src_count != 1:
            continue
        try:
            conf = float(info["frontmatter"].get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if conf <= 0.6:
            continue
        verify = info["frontmatter"].get("verify", False)
        if str(verify).lower() == "true":
            continue
        issues.append({
            "page": info["path"],
            "confidence": conf,
            "page_type": info["type"],
        })
    return issues


def check_unknown_category(wiki_root: Path, pages: dict) -> list[dict]:
    """Flag pages whose `category:` is not in the authoritative list.

    Per wiki/CATEGORIES.md (single source of truth since the 2026-05-13
    schema decomposition), categories are an enumerated taxonomy. Pages
    with an unknown category are a hard error — the agent cannot silently
    introduce new categories. Adding to the list requires editing
    CATEGORIES.md + bumping the schema version banner in AGENTS.md +
    appending a SCHEMA_CHANGELOG.md entry.

    Pages missing a `category:` field are caught by `check_missing_frontmatter`
    and not double-flagged here.
    """
    authoritative = load_authoritative_categories(wiki_root)
    issues: list[dict] = []
    for info in pages.values():
        category = info["frontmatter"].get("category")
        if category is None or not str(category).strip():
            continue  # missing-frontmatter check owns this
        if category not in authoritative:
            issues.append({"page": info["path"], "category": category})
    return issues


SUMMARY_WORD_THRESHOLD = 30
MID_SENTENCE_RE = re.compile(r"[.!?]\s+[A-Z]")


def check_long_summary(pages: dict) -> list[dict]:
    """Flag summaries that exceed ~30 words or contain more than one sentence.

    Rationale: update_index.py pulls the summary field directly into sub-index
    entries. Multi-sentence or bloated summaries degrade retrieval hints and
    make the index file harder to scan.
    """
    issues: list[dict] = []
    for info in pages.values():
        summary = info["frontmatter"].get("summary")
        if not isinstance(summary, str):
            continue
        summary_clean = summary.strip()
        if not summary_clean:
            continue
        word_count = len(summary_clean.split())
        # Multi-sentence heuristic: a sentence-terminal (., !, ?) followed by
        # whitespace and a capital letter, anywhere except the very end.
        body = re.sub(r"[.!?]+$", "", summary_clean)
        multi_sentence = bool(MID_SENTENCE_RE.search(body))
        if word_count > SUMMARY_WORD_THRESHOLD or multi_sentence:
            reasons = []
            if word_count > SUMMARY_WORD_THRESHOLD:
                reasons.append(f"{word_count} words (>{SUMMARY_WORD_THRESHOLD})")
            if multi_sentence:
                reasons.append("multi-sentence")
            issues.append({
                "page": info["path"],
                "reason": ", ".join(reasons),
                "word_count": word_count,
            })
    return issues


# Universal structural-artifact patterns. A page passes if any one fires.
# Single-instance patterns (one match is enough): fenced code, GFM table
# separator (the `|---|` row that must exist for a real table), attributed
# block-quote opener (`> "` or smart-quote variant).
ARTIFACT_PATTERNS_ANY: dict[str, re.Pattern] = {
    "fenced_code": re.compile(r"^```", re.MULTILINE),
    "table": re.compile(r"^\|[\s:|\-]+\|\s*$", re.MULTILINE),
    "attributed_quote": re.compile(r"^>\s*[\"“]", re.MULTILINE),
}
# Multi-instance patterns (≥2 matches): a single `1.` line isn't a procedure;
# a single `**term** — defn` line isn't a definition list.
ARTIFACT_PATTERNS_MULTI: dict[str, tuple[re.Pattern, int]] = {
    "numbered_step": (re.compile(r"^\d+\.\s+\S", re.MULTILINE), 2),
    "definition_list": (re.compile(r"^\*\*[^*\n]+\*\*\s*[—–\-]\s+\S", re.MULTILINE), 2),
}


def _strip_frontmatter(body: str) -> str:
    """Drop YAML frontmatter so its pipes don't false-positive the table check."""
    if body.startswith("---\n"):
        end = body.find("\n---\n", 4)
        if end != -1:
            return body[end + 5:]
    return body


def _has_artifact(body: str) -> bool:
    """True if body contains any structural artifact pattern."""
    text = _strip_frontmatter(body)
    for pat in ARTIFACT_PATTERNS_ANY.values():
        if pat.search(text):
            return True
    for pat, threshold in ARTIFACT_PATTERNS_MULTI.values():
        if len(pat.findall(text)) >= threshold:
            return True
    return False


def load_lint_config(wiki_root: Path) -> dict:
    """Load `<wiki>/.meta/lint-config.json`. Returns {} when absent or unparseable.

    Schema (currently): `{"artifact_check_categories": ["cat-a", "cat-b", ...]}`.
    Empty / missing allowlist means the artifact-presence check is silent —
    new wikis stay quiet by default until categories are explicitly opted in.
    """
    config_path = wiki_root / ".meta" / "lint-config.json"
    if not config_path.is_file():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def check_artifact_presence(pages: dict, config: dict) -> list[dict]:
    """Flag pages in opted-in categories that lack any structural artifact.

    Universal patterns recognized: fenced code blocks, GFM tables, numbered
    procedures (≥2 entries), block-quote attributions, definition lists
    (≥2 entries). A page passes if any one pattern is present.

    The check fires only for pages whose `category:` is in the
    `artifact_check_categories` allowlist of `.meta/lint-config.json`. Default
    empty allowlist → check is silent on fresh wikis (per the cross-domain
    framing: a philosophy or history wiki may legitimately have pure-prose
    pages; the user opts in specific categories rather than the script
    deciding).

    Source-summary pages are excluded — they're the raw-citation transit
    layer, not synthesized content where operational artifacts are expected.
    Soft-warning severity; never blocking.
    """
    allowlist = set(config.get("artifact_check_categories") or [])
    if not allowlist:
        return []
    issues: list[dict] = []
    for slug, info in pages.items():
        if info["type"] == "sources":
            continue
        category = info["frontmatter"].get("category")
        if category not in allowlist:
            continue
        if not _has_artifact(info["body"]):
            issues.append({"page": info["path"], "category": category})
    return issues


UNRESOLVED_HUB_THRESHOLD = 10


def check_unresolved_link_hubs(pages: dict, threshold: int = UNRESOLVED_HUB_THRESHOLD) -> list[dict]:
    """Slugs with >=threshold incoming [[wikilinks]] that don't resolve to a page.

    Soft-warning queue surfacing stub candidates for `/wiki-stub <slug>` (hub
    pattern). Counts self-references and aliased links the same as the body
    wikilink regex used elsewhere. Resolution check matches `check_broken_links`:
    target resolves iff `<slug>.md` exists in any of the five page-type dirs.

    Reuses the same regex shape as `list_unresolved_hubs.py` — duplicated
    intentionally for v1 per dev/wiki-stub-design.md. Refactor when a third
    caller appears.
    """
    inbound: dict[str, int] = {}
    page_slugs = set(pages.keys())
    for slug, info in pages.items():
        for target in find_wikilinks(info["body"]):
            target = target.strip()
            if not target or target == slug:
                continue
            inbound[target] = inbound.get(target, 0) + 1
    issues: list[dict] = []
    for target, count in inbound.items():
        if target in page_slugs:
            continue
        if count < threshold:
            continue
        issues.append({"slug": target, "inbound": count})
    issues.sort(key=lambda r: (-r["inbound"], r["slug"]))
    return issues


STUB_PROSE_MARKERS = [
    ("hub-section", "## Why this stub exists rather than silent absorption"),
    ("hub-callout", "⚠ needs verification — stub:"),  # ⚠ needs verification — stub:
    ("single-source-callout", "⚠ needs verification: single-source entity stub"),
    ("planned-callout", "⚠ planned research target"),
]


def check_drifted_stubs(pages: dict) -> list[dict]:
    """Pages whose body matches a stub-prose marker but lack `verify: true`.

    Forward shape-drift detector for the four verbatim stub markers (three
    callouts + the hub `## Why this stub exists` section heading). When any
    marker fires and `verify: true` is absent from frontmatter, the page has
    drifted off the codified stub contract — surface for backfill.

    Codified by dev/wiki-stub-design.md (2026-05-13). Predicate is verbatim
    string match against the four contracts in wiki/AGENTS.md § Stub callout
    templates. The codex shape-drift bug the lint exists to catch is closed
    inline (entities/codex.md gains the verbatim callout + verify:true), so
    expected hit count post-fix is 0; check is a forward sentinel for future
    drift.
    """
    issues: list[dict] = []
    for slug, info in pages.items():
        fm = info["frontmatter"]
        verify = fm.get("verify", False)
        if str(verify).lower() == "true":
            continue
        body = info["body"]
        for marker_id, marker in STUB_PROSE_MARKERS:
            if marker in body:
                issues.append({"page": info["path"], "marker": marker_id})
                break  # one hit per page is enough
    return issues


def _find_vault_dir(wiki_root: Path) -> Path:
    """Find the vault/ directory (sibling to wiki/)."""
    return wiki_root.parent / "vault"


def build_report(wiki_root: Path, vault_dir: Path | None = None) -> dict:
    if vault_dir is None:
        vault_dir = _find_vault_dir(wiki_root)
    pages = collect_pages(wiki_root)
    config = load_lint_config(wiki_root)
    return {
        "wiki_root": str(wiki_root),
        "vault_dir": str(vault_dir),
        "total_pages": len(pages),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "orphans": check_orphans(pages),
        "broken_links": check_broken_links(pages),
        "missing_frontmatter": check_missing_frontmatter(pages),
        "stale": check_stale(pages),
        "low_confidence": check_low_confidence(pages),
        "index_drift": check_index_drift(wiki_root, pages),
        "source_backlog": check_source_backlog(vault_dir, pages),
        "broken_sources": check_broken_sources(vault_dir, pages),
        "missing_source_type": check_missing_source_type(pages),
        "needs_verification": check_needs_verification(pages),
        "excessive_ambiguous": check_excessive_ambiguous(pages),
        "drifted_sources": check_drifted_sources(pages),
        "overview_staleness": check_overview_staleness(wiki_root),
        "single_page_categories": check_single_page_categories(pages),
        "unknown_category": check_unknown_category(wiki_root, pages),
        "single_source_unverified": check_single_source_unverified(pages),
        "long_summary": check_long_summary(pages),
        "stale_source": check_stale_source(pages),
        "asymmetric_contradictions": check_asymmetric_contradictions(pages),
        "invalid_typed_links": check_invalid_typed_links(pages),
        "artifact_presence": check_artifact_presence(pages, config),
        "unresolved_link_hubs": check_unresolved_link_hubs(pages),
        "drifted_stubs": check_drifted_stubs(pages),
    }


def render_human(report: dict) -> str:
    lines = [
        f"Wiki lint — {report['wiki_root']}",
        f"Checked at: {report['checked_at']}",
        f"Total pages: {report['total_pages']}",
        "",
        f"Orphans:              {len(report['orphans'])}",
        f"Broken links:         {len(report['broken_links'])}",
        f"Missing frontmatter:  {len(report['missing_frontmatter'])}",
        f"Stale:                {len(report['stale'])}",
        f"Low confidence:       {len(report['low_confidence'])}",
        f"Source backlog:       {len(report['source_backlog'])}",
        f"Broken sources:      {len(report['broken_sources'])}",
        f"Missing source_type:  {len(report['missing_source_type'])}",
        f"Needs verification:   {len(report['needs_verification'])}",
        f"Excessive AMBIGUOUS:  {len(report['excessive_ambiguous'])}",
        f"Drifted sources:      {len(report['drifted_sources'])}",
        f"Single-page cats:     {len(report['single_page_categories'])}",
        f"Unknown categories:   {len(report['unknown_category'])}",
        f"Single-source unverified: {len(report.get('single_source_unverified', []))}",
        f"Long summaries:       {len(report['long_summary'])}",
        f"Stale sources:        {len(report['stale_source'])}",
        f"Asymmetric contradicts: {len(report['asymmetric_contradictions'])}",
        f"Invalid typed links:  {len(report['invalid_typed_links'])}",
        f"Artifact presence:    {len(report['artifact_presence'])}",
        f"Unresolved link hubs: {len(report.get('unresolved_link_hubs', []))}",
        f"Drifted stubs:        {len(report.get('drifted_stubs', []))}",
    ]
    drift = report["index_drift"]
    if drift.get("missing_index"):
        lines.append("Index:                MISSING (no index-*.md files found)")
    else:
        drift_count = len(drift["in_pages_not_index"]) + len(drift["in_index_not_pages"])
        lines.append(f"Index drift:          {drift_count}")

    overview_stale = report.get("overview_staleness")
    if overview_stale:
        lines.append(f"Overview staleness:   {overview_stale['reason']}")
    else:
        lines.append("Overview staleness:   fresh")

    if report["broken_links"]:
        lines.append("")
        lines.append("Broken links:")
        for item in report["broken_links"][:20]:
            lines.append(f"  {item['page']} \u2192 [[{item['target']}]]")

    if report["missing_frontmatter"]:
        lines.append("")
        lines.append("Missing frontmatter fields:")
        for item in report["missing_frontmatter"][:20]:
            lines.append(f"  {item['page']}: {', '.join(item['missing'])}")

    if report["single_page_categories"]:
        lines.append("")
        lines.append("Categories with a single page:")
        for item in report["single_page_categories"][:20]:
            lines.append(f"  {item['page_type']}/{item['category']} \u2192 {item['page']}")

    if report.get("unknown_category"):
        lines.append("")
        lines.append("Unknown categories (not in the authoritative list \u2014 see wiki/CATEGORIES.md):")
        for item in report["unknown_category"][:20]:
            lines.append(f"  {item['page']}: {item['category']}")

    if report.get("single_source_unverified"):
        lines.append("")
        lines.append("Single-source pages with confidence > 0.6 and no verify flag (review queue):")
        for item in report["single_source_unverified"][:20]:
            lines.append(f"  {item['page']} (conf={item['confidence']})")

    if report["long_summary"]:
        lines.append("")
        lines.append("Long or multi-sentence summaries:")
        for item in report["long_summary"][:20]:
            lines.append(f"  {item['page']}: {item['reason']}")

    if report["stale_source"]:
        lines.append("")
        lines.append("Stale source pages (captured > 180 days ago or missing):")
        for item in report["stale_source"][:20]:
            lines.append(f"  {item['page']}: {item['reason']}")

    if report.get("asymmetric_contradictions"):
        lines.append("")
        lines.append("Asymmetric contradictions (A says contradicts B, but B doesn't list A):")
        for item in report["asymmetric_contradictions"][:20]:
            lines.append(f"  {item['page']} → [[{item['missing_inverse']}]] (inverse missing)")

    if report.get("invalid_typed_links"):
        lines.append("")
        lines.append("Invalid typed wikilinks (contradicts:/supersedes:/superseded_by:):")
        for item in report["invalid_typed_links"][:20]:
            lines.append(f"  {item['page']} {item['field']}: [[{item['target']}]] does not exist")

    if report.get("artifact_presence"):
        lines.append("")
        lines.append("Pages without structural artifacts (in opted-in categories):")
        for item in report["artifact_presence"][:20]:
            lines.append(f"  {item['page']} ({item['category']})")

    if report.get("unresolved_link_hubs"):
        lines.append("")
        lines.append(f"Unresolved link hubs (slugs with >={UNRESOLVED_HUB_THRESHOLD} inbound wikilinks, no page — stub candidates for /wiki-stub):")
        for item in report["unresolved_link_hubs"][:20]:
            lines.append(f"  [[{item['slug']}]] ({item['inbound']} inbound)")

    if report.get("drifted_stubs"):
        lines.append("")
        lines.append("Drifted stubs (stub-prose markers without verify:true — backfill needed):")
        for item in report["drifted_stubs"][:20]:
            lines.append(f"  {item['page']} (matched: {item['marker']})")

    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Mechanical lint for the wiki")
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument("--vault", default=None, help="Path to the vault/ directory (default: sibling of wiki root)")
    parser.add_argument("--json", action="store_true", help="Emit JSON (for subagent consumption)")
    args = parser.parse_args(argv)

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    vault_dir = Path(args.vault).resolve() if args.vault else None
    report = build_report(wiki_root, vault_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
