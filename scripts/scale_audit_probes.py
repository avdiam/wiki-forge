#!/usr/bin/env python3
"""Regenerate probe data for the Mode 7 (scale-breakdown) audit.

The Mode 7 audit (sourced from [[llm-wiki-failure-modes-deep-audit]]) names
four authoring-side sub-problems hybrid_search can't solve: edit-vs-create
candidate-reading cost, cascade completeness, concept-overlap drift, and
human navigability at high node count. This script regenerates the data
behind the audit's 5 probes so successive audits compare apples-to-apples.

The script is the cheap forward-insurance leg of the audit. It does NOT
make verdicts — those are judgment work that belong in a human-written
audit doc. Two of the five probes (concept-overlap drift, graph-view UX)
are inherently judgment-based; this script lists their spot-check
candidates so the runner doesn't have to recompute them, but the
agreement-checking and visual assessment stay manual.

Pure stdlib — matches the project's no-PyYAML/no-requests discipline.

Usage:
    python ${CLAUDE_PLUGIN_ROOT}/scripts/scale_audit_probes.py wiki
    python ${CLAUDE_PLUGIN_ROOT}/scripts/scale_audit_probes.py wiki --json
    python ${CLAUDE_PLUGIN_ROOT}/scripts/scale_audit_probes.py wiki --check-triggers
    python ${CLAUDE_PLUGIN_ROOT}/scripts/scale_audit_probes.py wiki --init --force \
        --total-pages 800 --verify-queue 250 --audit-doc dev/<date>-mode7-audit.md

Trigger thresholds are config-driven: read from `.meta/scale-audit-config.json`
(the live baseline, reset by `--init` after every audit), falling back to the
conservative DEFAULT_THRESHOLDS when no config exists (a fresh wiki). This
mirrors the overview-config.json / `check_overview_triggers.py --init` pattern —
without it, a latched trigger (e.g. total_pages, permanently past its threshold)
would nag the welcome report forever. `evaluate(wiki_root)` is the drill-facing
entry point that drill_state.py consumes for the `⚙ SCALE AUDIT DUE` banner.

The --check-triggers flag exits 1 if any re-audit trigger fires; 0 otherwise.
Triggers (thresholds shown are the conservative first-audit defaults; the live
baseline overrides them):
    1. total_pages >= 600
    2. verify queue composite: needs_verification >= 100 sustained across the
       last 3 valid lint runs AND latest (needs_verification +
       single_source_unverified) >= 250
    3. broken_links > 5 sustained across the last 5 valid lint runs
    4. updated/created Q4-vs-Q1 ratio inverts (Q4 < 1.5 OR Q4 < Q1)
    5. top concept hub exceeds 25_000 words (Read-limit proxy)
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from constants import PAGE_TYPES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")

# Default re-audit-trigger thresholds. These are the FALLBACK values used when
# no `.meta/scale-audit-config.json` baseline exists (a fresh wiki, which is
# always small enough that none fire). After every audit the runner resets the
# live baseline via `--init` to the forward thresholds the audit decided — so an
# already-fired latched trigger (e.g. total_pages, permanently past 600) stops
# nagging the welcome report. This mirrors the overview-config.json / Mode-B
# `check_overview_triggers.py --init` pattern exactly. Don't edit these to "fix"
# a stale fire — reset the config instead (run `/wiki-scale-audit`).
#
# History: the 600-page / 25K-word values are the 2026-05-08 first-audit
# thresholds; the 2026-06-06 second audit reset the live baseline to ≥800 pages
# and added the verify-queue composite to trigger 2 (see that audit's "Trigger
# reset" section). The defaults stay at the original first-audit values so a
# brand-new wiki inherits the conservative starting line.
TRIGGER_TOTAL_PAGES = 600
TRIGGER_NEEDS_VERIFICATION = 100
TRIGGER_NEEDS_VERIFICATION_RUNS = 3  # "sustained across 3+ lint runs"
TRIGGER_VERIFY_QUEUE = 250  # needs_verification + single_source_unverified, latest lint
TRIGGER_BROKEN_LINKS = 5  # strict inequality: > 5
TRIGGER_BROKEN_LINKS_RUNS = 5  # "sustained over 5+ lint runs"
TRIGGER_RATIO_FLOOR = 1.5
TRIGGER_TOP_CONCEPT_HUB_WORDS = 25_000  # word-count proxy for the Read limit

DEFAULT_THRESHOLDS = {
    "total_pages": TRIGGER_TOTAL_PAGES,
    "needs_verification": TRIGGER_NEEDS_VERIFICATION,
    "needs_verification_runs": TRIGGER_NEEDS_VERIFICATION_RUNS,
    "verify_queue": TRIGGER_VERIFY_QUEUE,
    "broken_links": TRIGGER_BROKEN_LINKS,
    "broken_links_runs": TRIGGER_BROKEN_LINKS_RUNS,
    "ratio_floor": TRIGGER_RATIO_FLOOR,
    "top_concept_hub_words": TRIGGER_TOP_CONCEPT_HUB_WORDS,
}

# Trigger 5 covers top *concept* hub only, not top entity hub. Entity pages
# naturally shed mass via `supersedes:` / version-specific sub-pages — a
# single product entity rarely needs to carry every version's detail forever.
# Concept pages don't have that pressure valve, so the structural-decomposition
# concern is specifically a concept-page concern. Don't "fix" this by
# extending to all hub types — the exclusion is intentional. See
# dev/2026-05-08-mode7-audit.md § Re-audit triggers and the matching
# Conditional entry in dev/tech-debt.md for the recorded decision.

# Spot-check budgets for the judgment probes.
SPOT_CHECK_HUB_COUNT = 3
SPOT_CHECK_LOW_COUNT = 3

# Lint-log window for trend analysis.
LINT_LOG_WINDOW = 25

# Ingest quartile for the updated/created ratio probe.
INGEST_QUARTILE_SIZE = 28


# Config baseline -------------------------------------------------------

def config_path(wiki_root: Path) -> Path:
    """Live re-audit baseline. Sibling of overview-config.json in .meta/."""
    return wiki_root / ".meta" / "scale-audit-config.json"


def load_config(wiki_root: Path) -> dict:
    """Return the on-disk baseline, or {} if none exists.

    No config is a valid state — a fresh wiki has never been audited. Callers
    fall back to DEFAULT_THRESHOLDS via load_thresholds().
    """
    cfg_path = config_path(wiki_root)
    if not cfg_path.is_file():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_thresholds(wiki_root: Path) -> dict:
    """Merge DEFAULT_THRESHOLDS with any config-stored overrides.

    Unknown keys in the config are ignored; missing keys fall back to defaults.
    This keeps a partial/older config forward-compatible with new thresholds.
    """
    thresholds = dict(DEFAULT_THRESHOLDS)
    stored = load_config(wiki_root).get("thresholds")
    if isinstance(stored, dict):
        for k in thresholds:
            if k in stored and isinstance(stored[k], (int, float)):
                thresholds[k] = stored[k]
    return thresholds


def init_config(
    wiki_root: Path,
    overrides: dict | None = None,
    audit_doc: str | None = None,
    audit_date: str | None = None,
    force: bool = False,
) -> int:
    """Write the re-audit baseline. Run by /wiki-scale-audit after an audit to
    reset the forward triggers the audit decided (mirrors
    check_overview_triggers.py --init).

    `overrides` set the next firing's thresholds; absent keys keep the current
    defaults. `audit_date` defaults to today; `audit_doc` records the dated
    audit doc this baseline was reset from.
    """
    cfg_path = config_path(wiki_root)
    if cfg_path.exists() and not force:
        print(f"error: {cfg_path} already exists — use --force to overwrite", file=sys.stderr)
        return 2
    thresholds = dict(DEFAULT_THRESHOLDS)
    for k, v in (overrides or {}).items():
        if k in thresholds and v is not None:
            thresholds[k] = v
    cfg = {
        "last_audit_date": audit_date or date.today().isoformat(),
        "last_audit_doc": audit_doc,
        "thresholds": thresholds,
    }
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"scale-audit baseline written to {cfg_path}")
    print(f"  last audit: {cfg['last_audit_date']} ({cfg['last_audit_doc'] or 'no doc'})")
    print(f"  thresholds: {thresholds}")
    return 0


def _content_pages(wiki_root: Path) -> dict[str, Path]:
    """Map slug -> path for every wiki content page (sources / concepts /
    entities / comparisons / syntheses)."""
    pages: dict[str, Path] = {}
    for page_type in PAGE_TYPES:
        page_dir = wiki_root / "pages" / page_type
        if not page_dir.is_dir():
            continue
        for p in page_dir.glob("*.md"):
            pages[p.stem] = p
    return pages


def _inbound_counts(pages: dict[str, Path]) -> Counter:
    """Deduped inbound count per slug. Multiple links from the same source
    page count once — matches graph_analyzer.py's edge semantics."""
    inbound: Counter = Counter()
    for src_slug, p in pages.items():
        text = p.read_text(encoding="utf-8", errors="ignore")
        seen: set[str] = set()
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).strip()
            if target == src_slug or target in seen:
                continue
            if target in pages:
                inbound[target] += 1
                seen.add(target)
    return inbound


# Probe 1 ---------------------------------------------------------------

def probe_inbound_distribution(pages: dict[str, Path], inbound: Counter) -> dict:
    """Probe 1 — full inbound distribution + top-20 hubs.

    Sub-problem #4 leading indicator (hub saturation). Compare bin counts
    and top-N hub list against the prior audit's snapshot to spot
    saturation-tipping (e.g., new pages crossing 100 inbound).
    """
    bins_def = [
        (150, ">=150"), (100, "100-149"), (50, "50-99"), (25, "25-49"),
        (10, "10-24"), (5, "5-9"), (1, "1-4"), (0, "0"),
    ]
    counts: dict[str, int] = {label: 0 for _, label in bins_def}
    for slug in pages:
        c = inbound.get(slug, 0)
        for thresh, label in bins_def:
            if c >= thresh:
                counts[label] += 1
                break

    top_hubs = []
    for slug, c in sorted(inbound.items(), key=lambda x: -x[1])[:20]:
        top_hubs.append({
            "slug": slug,
            "type": pages[slug].parent.name,
            "inbound": c,
        })

    type_breakdown = Counter(p.parent.name for p in pages.values())

    return {
        "total_pages": len(pages),
        "bins": counts,
        "top_hubs": top_hubs,
        "type_breakdown": dict(type_breakdown),
        "top1_inbound_pct_of_pages": round(100.0 * (top_hubs[0]["inbound"] if top_hubs else 0) / max(len(pages), 1), 1),
    }


# Probe 2 ---------------------------------------------------------------

def probe_overlap_candidates(pages: dict[str, Path], inbound: Counter) -> dict:
    """Probe 2 — concept-overlap drift spot-check candidates.

    JUDGMENT PROBE. This script does NOT decide whether neighboring pages
    agree on shared claims; it only lists the candidate set so the runner
    has something to start reading. Pick top-N concept hubs + lowest-N
    concept pages and emit each one's 1-hop neighbour set as candidate
    pairs to compare for drift.

    Returns dict shaped for the audit doc's prose: per slug, the slug
    itself, its inbound count, its outbound wikilink set (capped), and
    a hint about which neighbours look topically adjacent.
    """
    concepts = {s: p for s, p in pages.items() if p.parent.name == "concepts"}
    sorted_concepts = sorted(
        concepts.items(),
        key=lambda kv: -inbound.get(kv[0], 0),
    )
    high_traffic = sorted_concepts[:SPOT_CHECK_HUB_COUNT]
    # Skip slugs with 0 inbound — they're orphans, not low-traffic-but-
    # connected. Take the lowest-N with at least 1 inbound.
    low_traffic = [
        (s, p) for s, p in sorted_concepts
        if inbound.get(s, 0) >= 1
    ][-SPOT_CHECK_LOW_COUNT:]

    def _outbound_neighbors(p: Path, src_slug: str, cap: int = 12) -> list[str]:
        text = p.read_text(encoding="utf-8", errors="ignore")
        seen: list[str] = []
        seen_set: set[str] = set()
        for m in WIKILINK_RE.finditer(text):
            t = m.group(1).strip()
            if t == src_slug or t in seen_set or t not in pages:
                continue
            seen.append(t)
            seen_set.add(t)
            if len(seen) >= cap:
                break
        return seen

    candidates = []
    for slug, p in high_traffic + low_traffic:
        candidates.append({
            "slug": slug,
            "tier": "high" if (slug, p) in high_traffic else "low",
            "inbound": inbound.get(slug, 0),
            "neighbours": _outbound_neighbors(p, slug),
        })

    return {
        "n_concepts_total": len(concepts),
        "candidates": candidates,
        "instructions": (
            "JUDGMENT PROBE. For each candidate, read the page + 1-3 of its "
            "listed neighbours. Check whether neighbours agree with the "
            "candidate on shared claims. Drift = subtle disagreement that "
            "doesn't break the lint. Output goes in the audit doc's "
            "Probe 2 section, NOT regenerated by this script."
        ),
    }


# Probe 3 ---------------------------------------------------------------

def probe_lint_trend(wiki_root: Path) -> dict:
    """Probe 3 — lint_log trend over the last LINT_LOG_WINDOW rows.

    Sub-problem #2 (cascade completeness) signal. Tracks orphans /
    single_page_categories / needs_verification / broken_links over time;
    rising trends are the mechanical surface of the failure mode.
    """
    log_path = wiki_root / ".meta" / "lint_log.jsonl"
    if not log_path.is_file():
        return {"available": False, "reason": "lint_log.jsonl not present"}

    rows = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not rows:
        return {"available": False, "reason": "lint_log.jsonl is empty"}

    window = rows[-LINT_LOG_WINDOW:]
    keys = (
        "orphans", "broken_links", "single_page_categories",
        "needs_verification", "single_source_unverified",
        "excessive_ambiguous", "drifted_sources",
    )

    def _row_value(r: dict, k: str):
        # Some early rows nest counts under "counts:"; later rows top-level.
        if k in r:
            return r[k]
        counts = r.get("counts") or {}
        return counts.get(k)

    series: dict[str, list] = {k: [] for k in keys}
    for r in window:
        for k in keys:
            series[k].append(_row_value(r, k))

    def _trend(vals: list) -> dict:
        cleaned = [v for v in vals if isinstance(v, (int, float))]
        if not cleaned:
            return {"first": None, "last": None, "delta": None, "n": 0}
        return {
            "first": cleaned[0],
            "last": cleaned[-1],
            "delta": cleaned[-1] - cleaned[0],
            "n": len(cleaned),
        }

    return {
        "available": True,
        "window_size": len(window),
        "earliest_timestamp": window[0].get("timestamp"),
        "latest_timestamp": window[-1].get("timestamp"),
        "trends": {k: _trend(v) for k, v in series.items()},
        "series": series,  # raw per-row values for sustained-condition triggers
    }


# Probe 4 ---------------------------------------------------------------

def probe_ingest_ratio(wiki_root: Path) -> dict:
    """Probe 4 — pages_created vs pages_updated trend, quartile-sliced.

    Sub-problem #1 (edit-vs-create cost) signal. As the wiki matures, the
    ingestor should weave new sources into existing pages, not spawn new
    ones — updated/created ratio should rise. Inverting (Q4 < Q1, or
    Q4 < TRIGGER_RATIO_FLOOR) means the ingestor is regressing on
    edit-vs-create discipline.
    """
    log_path = wiki_root / ".meta" / "ingest_log.jsonl"
    if not log_path.is_file():
        return {"available": False, "reason": "ingest_log.jsonl not present"}

    rows = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if len(rows) < 4:
        return {"available": False, "reason": f"too few ingests for quartile analysis (n={len(rows)})"}

    def _slice_stats(slice_rows: list) -> dict:
        cr = [len(r.get("pages_created", [])) for r in slice_rows]
        up = [len(r.get("pages_updated", [])) for r in slice_rows]
        ratio = sum(up) / max(sum(cr), 1)
        return {
            "n": len(slice_rows),
            "avg_created": round(statistics.mean(cr), 2) if cr else 0,
            "avg_updated": round(statistics.mean(up), 2) if up else 0,
            "updated_per_created": round(ratio, 2),
        }

    n = len(rows)
    quartiles = {
        "Q1_oldest": _slice_stats(rows[: n // 4]),
        "Q2": _slice_stats(rows[n // 4 : 2 * n // 4]),
        "Q3": _slice_stats(rows[2 * n // 4 : 3 * n // 4]),
        "Q4_newest": _slice_stats(rows[3 * n // 4 :]),
    }

    return {
        "available": True,
        "total_rows": n,
        "quartiles": quartiles,
        "trajectory": (
            "improving" if quartiles["Q4_newest"]["updated_per_created"] > quartiles["Q1_oldest"]["updated_per_created"]
            else "regressing" if quartiles["Q4_newest"]["updated_per_created"] < quartiles["Q1_oldest"]["updated_per_created"]
            else "flat"
        ),
    }


# Probe 5 ---------------------------------------------------------------

def probe_graph_metadata(pages: dict[str, Path], inbound: Counter) -> dict:
    """Probe 5 — graph metadata for the navigability assessment.

    JUDGMENT PROBE for the visual assessment (graph-view UX from screenshot
    is human work). This script emits the metadata that informs the
    judgment: hub concentration, edge density, components count, and the
    long-tail shape that determines whether a graph view is "dense but
    legible" or "decorative."
    """
    total_pages = len(pages)
    total_edges = sum(inbound.values())

    # Compute connected components on the undirected graph.
    adj: dict[str, set[str]] = {s: set() for s in pages}
    for src_slug, p in pages.items():
        text = p.read_text(encoding="utf-8", errors="ignore")
        seen: set[str] = set()
        for m in WIKILINK_RE.finditer(text):
            t = m.group(1).strip()
            if t == src_slug or t in seen or t not in pages:
                continue
            adj[src_slug].add(t)
            adj[t].add(src_slug)
            seen.add(t)

    visited: set[str] = set()
    components = 0
    largest_component = 0
    for slug in pages:
        if slug in visited:
            continue
        components += 1
        # BFS
        stack = [slug]
        size = 0
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            size += 1
            stack.extend(adj[node] - visited)
        largest_component = max(largest_component, size)

    orphans = sum(1 for s in pages if not adj[s])

    return {
        "total_pages": total_pages,
        "total_edges": total_edges,
        "avg_inbound": round(total_edges / max(total_pages, 1), 1),
        "components": components,
        "largest_component": largest_component,
        "orphans": orphans,
        "top1_inbound_pct_of_pages": round(100.0 * (max(inbound.values()) if inbound else 0) / max(total_pages, 1), 1),
        "top1_share_of_edges_pct": round(100.0 * (max(inbound.values()) if inbound else 0) / max(total_edges, 1), 1),
        "instructions": (
            "JUDGMENT PROBE. Open the wiki in Obsidian, capture a graph-view "
            "screenshot, and assess legibility against the metadata above. "
            "Key questions: are hub nodes still distinguishable? Are edges "
            "still resolvable to individual lines, or have they merged into "
            "a haze? Is the graph one giant component or fragmenting?"
        ),
    }


# Re-audit triggers -----------------------------------------------------

def _latest(series: list) -> int | float | None:
    """Last non-null numeric value in a series, or None."""
    for v in reversed(series):
        if isinstance(v, (int, float)):
            return v
    return None


def check_triggers(probes: dict, pages: dict[str, Path], thresholds: dict | None = None) -> dict:
    """Check the 5 hard re-audit triggers.

    Thresholds come from the live `.meta/scale-audit-config.json` baseline
    (via load_thresholds) so each audit can reset them forward; absent config
    falls back to DEFAULT_THRESHOLDS. Returns dict with per-trigger fired
    status + the threshold + the actual value. Trigger 4 (ratio inversion)
    uses the Q1/Q4 comparison; the others are single-snapshot or sustained.
    """
    t = thresholds or DEFAULT_THRESHOLDS
    triggers = []

    # 1: total pages
    total = probes["inbound"]["total_pages"]
    triggers.append({
        "id": 1,
        "name": "total_pages_threshold",
        "fired": total >= t["total_pages"],
        "threshold": t["total_pages"],
        "actual": total,
    })

    # 2: composite — needs_verification sustained across N runs AND the latest
    # verify queue (needs_verification + single_source_unverified) at/over the
    # queue threshold. The 2026-06-06 reset added the queue arm: a sustained-but-
    # small nv shouldn't fire on its own (the v1.x corroborate drain handles
    # that); the audit re-fires only when the *queue* is genuinely deep. Filter
    # nulls BEFORE slicing so a malformed lint_log row can't collapse the window.
    series = probes["lint_trend"].get("series", {})
    nv_series = series.get("needs_verification", [])
    nv_clean = [v for v in nv_series if isinstance(v, (int, float))]
    nv_runs = t["needs_verification_runs"]
    nv_window = nv_clean[-nv_runs:]
    nv_sustained = (
        len(nv_window) >= nv_runs
        and all(v >= t["needs_verification"] for v in nv_window)
    )
    latest_nv = _latest(nv_series)
    latest_ssu = _latest(series.get("single_source_unverified", []))
    verify_queue = None
    if latest_nv is not None:
        verify_queue = latest_nv + (latest_ssu or 0)
    queue_deep = verify_queue is not None and verify_queue >= t["verify_queue"]
    triggers.append({
        "id": 2,
        "name": "verify_queue_sustained",
        "fired": bool(nv_sustained and queue_deep),
        "threshold_needs_verification": t["needs_verification"],
        "threshold_verify_queue": t["verify_queue"],
        "runs_required": nv_runs,
        "nv_last_n_values": nv_window,
        "verify_queue": verify_queue,
        "nv_sustained": nv_sustained,
    })

    # 3: broken_links sustained across N runs. Same null-filter-before-slice
    # discipline as trigger 2.
    bl_series = series.get("broken_links", [])
    bl_clean = [v for v in bl_series if isinstance(v, (int, float))]
    bl_runs = t["broken_links_runs"]
    bl_window = bl_clean[-bl_runs:]
    fired_3 = (
        len(bl_window) >= bl_runs
        and all(v > t["broken_links"] for v in bl_window)
    )
    triggers.append({
        "id": 3,
        "name": "broken_links_sustained",
        "fired": fired_3,
        "threshold": t["broken_links"],
        "runs_required": bl_runs,
        "last_n_values": bl_window,
        "latest": bl_window[-1] if bl_window else None,
    })

    # 4: ratio inversion
    qs = probes["ingest_ratio"].get("quartiles", {})
    q1 = qs.get("Q1_oldest", {}).get("updated_per_created")
    q4 = qs.get("Q4_newest", {}).get("updated_per_created")
    fired_4 = False
    if isinstance(q1, (int, float)) and isinstance(q4, (int, float)):
        fired_4 = q4 < t["ratio_floor"] or q4 < q1
    triggers.append({
        "id": 4,
        "name": "ratio_inversion",
        "fired": fired_4,
        "threshold_floor": t["ratio_floor"],
        "Q1": q1,
        "Q4": q4,
    })

    # 5: top concept hub exceeds Read-limit word-count proxy.
    # Generalized from the 2026-05-08 audit's specific finding on
    # `agent-harness`. The structural concern is universal: a high-traffic
    # concept page that has grown past the Read limit can no longer be
    # routinely opened mid-ingest, which forces structural decomposition.
    hub_words_threshold = t["top_concept_hub_words"]
    top_concept_hub = next(
        (h for h in probes["inbound"]["top_hubs"] if h["type"] == "concepts"),
        None,
    )
    hub_words = None
    if top_concept_hub:
        hub_path = pages.get(top_concept_hub["slug"])
        if hub_path:
            hub_words = len(hub_path.read_text(encoding="utf-8", errors="ignore").split())
    triggers.append({
        "id": 5,
        "name": "top_concept_hub_size_proxy",
        "fired": isinstance(hub_words, int) and hub_words >= hub_words_threshold,
        "threshold_words": hub_words_threshold,
        "hub_slug": top_concept_hub["slug"] if top_concept_hub else None,
        "hub_words": hub_words,
        "note": "word-count proxy for the 25K-token Read limit",
    })

    return {
        "any_fired": any(t_["fired"] for t_ in triggers),
        "fired_count": sum(1 for t_ in triggers if t_["fired"]),
        "triggers": triggers,
    }


# Drill-facing evaluation -----------------------------------------------

def _reason_string(trig: dict) -> str:
    """Short human-readable reason for a fired trigger (welcome-report banner)."""
    tid = trig["id"]
    if tid == 1:
        return f"total_pages {trig['actual']}≥{trig['threshold']}"
    if tid == 2:
        return f"verify_queue {trig['verify_queue']}≥{trig['threshold_verify_queue']} (nv sustained)"
    if tid == 3:
        return f"broken_links sustained >{trig['threshold']}"
    if tid == 4:
        return f"ratio inversion (Q4 {trig['Q4']} < floor {trig['threshold_floor']} or < Q1 {trig['Q1']})"
    if tid == 5:
        return f"concept hub {trig['hub_slug']} {trig['hub_words']}w≥{trig['threshold_words']}"
    return trig["name"]


def evaluate(wiki_root: Path) -> dict:
    """Compute the re-audit verdict for the wake-up drill.

    Self-contained (own page walk, ~0.3s) — mirrors check_overview_triggers.evaluate.
    Returns a compact block the welcome-report renderer maps to the
    `⚙ SCALE AUDIT DUE` banner + offer line.
    """
    pages = _content_pages(wiki_root)
    inbound = _inbound_counts(pages)
    probes = {
        "inbound": probe_inbound_distribution(pages, inbound),
        "lint_trend": probe_lint_trend(wiki_root),
        "ingest_ratio": probe_ingest_ratio(wiki_root),
    }
    thresholds = load_thresholds(wiki_root)
    result = check_triggers(probes, pages, thresholds)
    cfg = load_config(wiki_root)
    fired = [tr for tr in result["triggers"] if tr["fired"]]
    return {
        "should_audit": result["any_fired"],
        "fired_count": result["fired_count"],
        "reasons": [_reason_string(tr) for tr in fired],
        "last_audit_date": cfg.get("last_audit_date"),
        "configured": bool(cfg),
    }


# Output formatting -----------------------------------------------------

def _format_text(probes: dict, triggers: dict) -> str:
    out: list[str] = []
    out.append("=" * 72)
    out.append("Mode 7 (scale-breakdown) audit — probe data")
    out.append("=" * 72)

    # Probe 1
    out.append("")
    out.append("## Probe 1 — Inbound-link distribution")
    p1 = probes["inbound"]
    out.append(f"Total content pages: {p1['total_pages']}")
    out.append(f"Type breakdown: {p1['type_breakdown']}")
    out.append(f"Top hub: linked from {p1['top1_inbound_pct_of_pages']}% of all pages")
    out.append("Bins:")
    for label, count in p1["bins"].items():
        bar = "#" * min(count // 2, 60)
        out.append(f"  {label:<8} {count:4d}  {bar}")
    out.append("Top-15 hubs:")
    for h in p1["top_hubs"][:15]:
        out.append(f"  {h['inbound']:3d}  {h['type']:<12} {h['slug']}")

    # Probe 2 candidates
    out.append("")
    out.append("## Probe 2 — Concept-overlap drift spot-check candidates [JUDGMENT]")
    p2 = probes["overlap_candidates"]
    out.append(f"Concepts in vault: {p2['n_concepts_total']}")
    for c in p2["candidates"]:
        out.append(f"  [{c['tier']:<4}] {c['inbound']:3d}  {c['slug']}")
        out.append(f"        neighbours: {', '.join(c['neighbours'][:8])}")

    # Probe 3
    out.append("")
    out.append("## Probe 3 — Lint trend (last %d rows)" % LINT_LOG_WINDOW)
    p3 = probes["lint_trend"]
    if p3.get("available"):
        out.append(f"Window: {p3['earliest_timestamp']} -> {p3['latest_timestamp']} (n={p3['window_size']})")
        for k, t in p3["trends"].items():
            if t["n"]:
                out.append(f"  {k:<24} first={t['first']} last={t['last']} delta={t['delta']:+}")
    else:
        out.append(f"Unavailable: {p3.get('reason')}")

    # Probe 4
    out.append("")
    out.append("## Probe 4 — Ingest pages_created/updated quartiles")
    p4 = probes["ingest_ratio"]
    if p4.get("available"):
        out.append(f"Total ingest rows: {p4['total_rows']} | Trajectory: {p4['trajectory']}")
        for label, s in p4["quartiles"].items():
            out.append(f"  {label:<11} n={s['n']:<3} created={s['avg_created']:<5} updated={s['avg_updated']:<5} ratio={s['updated_per_created']}")
    else:
        out.append(f"Unavailable: {p4.get('reason')}")

    # Probe 5
    out.append("")
    out.append("## Probe 5 — Graph metadata for UX assessment [JUDGMENT]")
    p5 = probes["graph_meta"]
    out.append(f"Pages={p5['total_pages']} | edges={p5['total_edges']} | avg_inbound={p5['avg_inbound']}")
    out.append(f"Components={p5['components']} (largest={p5['largest_component']}) | orphans={p5['orphans']}")
    out.append(f"Top hub: linked from {p5['top1_inbound_pct_of_pages']}% of all pages | {p5['top1_share_of_edges_pct']}% of all edges land here")

    # Triggers
    out.append("")
    out.append("## Re-audit triggers")
    out.append(f"Any fired: {triggers['any_fired']} ({triggers['fired_count']}/5)")
    for t in triggers["triggers"]:
        marker = "FIRED" if t["fired"] else "ok   "
        out.append(f"  [{marker}] {t['id']}. {t['name']}")
        if t["fired"]:
            for k, v in t.items():
                if k in ("id", "name", "fired"):
                    continue
                out.append(f"          {k}: {v}")

    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wiki_root", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument(
        "--check-triggers", action="store_true",
        help="Exit 1 if any of the (config-baselined) re-audit triggers fire; 0 otherwise.",
    )
    parser.add_argument(
        "--init", action="store_true",
        help="Write/reset the .meta/scale-audit-config.json baseline (run by "
        "/wiki-scale-audit after an audit). Use threshold flags to set the next "
        "firing's thresholds; absent flags keep the conservative defaults.",
    )
    parser.add_argument("--force", action="store_true", help="With --init: overwrite an existing config.")
    parser.add_argument("--audit-doc", help="With --init: path to the dated audit doc this baseline resets from.")
    parser.add_argument("--audit-date", help="With --init: ISO date of the audit (default: today).")
    # Threshold override flags for --init.
    parser.add_argument("--total-pages", type=int)
    parser.add_argument("--needs-verification", type=int)
    parser.add_argument("--needs-verification-runs", type=int)
    parser.add_argument("--verify-queue", type=int)
    parser.add_argument("--broken-links", type=int)
    parser.add_argument("--broken-links-runs", type=int)
    parser.add_argument("--ratio-floor", type=float)
    parser.add_argument("--top-concept-hub-words", type=int)
    args = parser.parse_args()

    wiki_root = args.wiki_root.resolve()
    if not wiki_root.is_dir():
        print(f"error: wiki_root not a directory: {wiki_root}", file=sys.stderr)
        return 2

    if args.init:
        overrides = {
            "total_pages": args.total_pages,
            "needs_verification": args.needs_verification,
            "needs_verification_runs": args.needs_verification_runs,
            "verify_queue": args.verify_queue,
            "broken_links": args.broken_links,
            "broken_links_runs": args.broken_links_runs,
            "ratio_floor": args.ratio_floor,
            "top_concept_hub_words": args.top_concept_hub_words,
        }
        return init_config(
            wiki_root,
            overrides={k: v for k, v in overrides.items() if v is not None},
            audit_doc=args.audit_doc,
            audit_date=args.audit_date,
            force=args.force,
        )

    pages = _content_pages(wiki_root)
    inbound = _inbound_counts(pages)

    probes = {
        "inbound": probe_inbound_distribution(pages, inbound),
        "overlap_candidates": probe_overlap_candidates(pages, inbound),
        "lint_trend": probe_lint_trend(wiki_root),
        "ingest_ratio": probe_ingest_ratio(wiki_root),
        "graph_meta": probe_graph_metadata(pages, inbound),
    }
    thresholds = load_thresholds(wiki_root)
    triggers = check_triggers(probes, pages, thresholds)

    if args.json:
        print(json.dumps({"probes": probes, "triggers": triggers, "thresholds": thresholds}, indent=2))
    else:
        print(_format_text(probes, triggers))

    if args.check_triggers and triggers["any_fired"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
