#!/usr/bin/env python3
"""Detect whether wiki/overview.md needs a full Mode B rewrite.

Compares current wiki state against a baseline snapshot stored in
wiki/.meta/overview-config.json (set by the last full rewrite). Returns
JSON `{should_rewrite: bool, reasons: [str], current_state: {...}}`.

Triggers (any one fires `should_rewrite: true`):
- Top-3 hub reshuffle since baseline (membership change in top 3 by
  inbound wikilinks)
- ≥`hub_flip_threshold_pct`% inbound-count shift on any current top-3
  hub vs its baseline value
- ≥3 contradiction edges accumulated since baseline
- A new category appeared (first page in a category not in the
  baseline's `known_categories` list)
- ≥`emergency_floor_months` months elapsed since the baseline date

If the config file is missing, prints a message and exits with `should_rewrite: true`
and reason `"no baseline snapshot — initialize first"`.

Use `--init` to seed the config from current state. Use after a full
Mode B rewrite to reset the baseline.

Pure stdlib + invokes graph_analyzer.py via subprocess.

Usage:
    python check_overview_triggers.py <wiki-root>
    python check_overview_triggers.py <wiki-root> --init
    python check_overview_triggers.py <wiki-root> --graph-json <path-or-->  # reuse precomputed graph
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

# Local imports from sibling .scripts modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from constants import PAGE_TYPES  # noqa: E402
from frontmatter import parse_frontmatter  # noqa: E402
from graph_analyzer import analyze as analyze_graph  # noqa: E402
from log_parsers import count_ingests  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_HUB_FLIP_PCT = 30
DEFAULT_EMERGENCY_FLOOR_MONTHS = 6
DEFAULT_CONTRADICTION_DELTA = 3


def config_path(wiki_root: Path) -> Path:
    return wiki_root / ".meta" / "overview-config.json"


def load_graph(wiki_root: Path, graph_json_arg: str | None = None) -> dict:
    """Return graph stats — from a precomputed JSON if provided, else rebuild.

    `graph_json_arg` may be a file path or `-` for stdin. Lets callers
    (e.g., the wake-up drill) pass a graph snapshot they already computed,
    avoiding a second full-disk rebuild.
    """
    if graph_json_arg == "-":
        return json.loads(sys.stdin.read())
    if graph_json_arg:
        return json.loads(Path(graph_json_arg).read_text(encoding="utf-8"))
    return analyze_graph(wiki_root, top=10)


def collect_categories(wiki_root: Path) -> set[str]:
    cats: set[str] = set()
    for t in PAGE_TYPES:
        d = wiki_root / "pages" / t
        if not d.is_dir():
            continue
        for md in d.glob("*.md"):
            try:
                fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            cat = fm.get("category")
            if isinstance(cat, str) and cat.strip():
                cats.add(cat.strip())
    return cats


def current_state(wiki_root: Path, graph_json_arg: str | None = None) -> dict:
    graph = load_graph(wiki_root, graph_json_arg)
    top3 = [
        {"page": h["page"], "inbound": h["inbound"]}
        for h in graph["top_hubs_inbound"][:3]
    ]
    typed = graph.get("typed_edges", {})
    return {
        "ingest_count": count_ingests(wiki_root),
        "date": date.today().isoformat(),
        "top_hubs": top3,
        "contradiction_edges": typed.get("contradicts", 0),
        "categories": sorted(collect_categories(wiki_root)),
    }


def init_config(wiki_root: Path, force: bool = False, graph_json_arg: str | None = None) -> int:
    cfg_path = config_path(wiki_root)
    if cfg_path.exists() and not force:
        print(f"error: {cfg_path} already exists — use --force to overwrite", file=sys.stderr)
        return 2
    state = current_state(wiki_root, graph_json_arg)
    cfg = {
        "last_full_rewrite_ingest": state["ingest_count"],
        "last_full_rewrite_date": state["date"],
        "last_full_rewrite_top_hubs": state["top_hubs"],
        "last_full_rewrite_contradictions": state["contradiction_edges"],
        "known_categories": state["categories"],
        "hub_flip_threshold_pct": DEFAULT_HUB_FLIP_PCT,
        "contradiction_delta_threshold": DEFAULT_CONTRADICTION_DELTA,
        "emergency_floor_months": DEFAULT_EMERGENCY_FLOOR_MONTHS,
    }
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"baseline written to {cfg_path}")
    print(f"  ingest #{cfg['last_full_rewrite_ingest']} on {cfg['last_full_rewrite_date']}")
    hubs_str = ", ".join(
        f"{h['page']} ({h['inbound']})" for h in cfg["last_full_rewrite_top_hubs"]
    )
    print(f"  top hubs: {hubs_str}")
    return 0


def months_between(iso_a: str, iso_b: str) -> float:
    d_a = datetime.fromisoformat(iso_a).date()
    d_b = datetime.fromisoformat(iso_b).date()
    return (d_b - d_a).days / 30.4375


def evaluate(wiki_root: Path, graph_json_arg: str | None = None) -> dict:
    cfg_path_ = config_path(wiki_root)
    state = current_state(wiki_root, graph_json_arg)

    if not cfg_path_.is_file():
        return {
            "should_rewrite": True,
            "reasons": ["no baseline snapshot — initialize with --init after the next full rewrite"],
            "current_state": state,
            "baseline": None,
        }

    cfg = json.loads(cfg_path_.read_text(encoding="utf-8"))
    reasons: list[str] = []

    baseline_top3 = {h["page"]: h["inbound"] for h in cfg.get("last_full_rewrite_top_hubs", [])}
    current_top3 = {h["page"]: h["inbound"] for h in state["top_hubs"]}

    # Trigger 1: top-3 reshuffle (membership change)
    if set(baseline_top3.keys()) != set(current_top3.keys()):
        added = sorted(set(current_top3) - set(baseline_top3))
        removed = sorted(set(baseline_top3) - set(current_top3))
        reasons.append(
            f"top-3 hub reshuffle since baseline — added: [{', '.join(added) or 'none'}], "
            f"removed: [{', '.join(removed) or 'none'}]"
        )

    # Trigger 2: ≥N% inbound shift on any current top-3 hub vs baseline
    threshold_pct = cfg.get("hub_flip_threshold_pct", DEFAULT_HUB_FLIP_PCT)
    for slug, current_n in current_top3.items():
        baseline_n = baseline_top3.get(slug)
        if baseline_n is None or baseline_n == 0:
            continue
        shift_pct = abs(current_n - baseline_n) / baseline_n * 100
        if shift_pct >= threshold_pct:
            reasons.append(
                f"hub `{slug}` inbound shifted {shift_pct:.0f}% "
                f"(baseline {baseline_n} → current {current_n}, threshold {threshold_pct}%)"
            )

    # Trigger 3: ≥N contradiction edges accumulated since baseline
    delta_threshold = cfg.get("contradiction_delta_threshold", DEFAULT_CONTRADICTION_DELTA)
    contradiction_delta = state["contradiction_edges"] - cfg.get("last_full_rewrite_contradictions", 0)
    if contradiction_delta >= delta_threshold:
        reasons.append(
            f"{contradiction_delta} new contradiction edges since baseline "
            f"(threshold {delta_threshold})"
        )

    # Trigger 4: new category first-page detected
    known_cats = set(cfg.get("known_categories", []))
    new_cats = sorted(set(state["categories"]) - known_cats)
    if new_cats:
        reasons.append(f"new categor{'ies' if len(new_cats) > 1 else 'y'} appeared: {', '.join(new_cats)}")

    # Trigger 5: emergency floor — ≥N months since baseline
    floor_months = cfg.get("emergency_floor_months", DEFAULT_EMERGENCY_FLOOR_MONTHS)
    elapsed_months = months_between(cfg["last_full_rewrite_date"], state["date"])
    if elapsed_months >= floor_months:
        reasons.append(
            f"emergency floor — {elapsed_months:.1f} months since last full rewrite "
            f"(floor {floor_months})"
        )

    return {
        "should_rewrite": bool(reasons),
        "reasons": reasons,
        "current_state": state,
        "baseline": cfg,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect whether wiki/overview.md needs a full Mode B rewrite."
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Seed overview-config.json from current state. Run after a full Mode B rewrite.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --init: overwrite an existing config file.",
    )
    parser.add_argument(
        "--graph-json",
        help="Path to a precomputed graph_analyzer.py --json output, or '-' for stdin. "
        "Avoids re-rebuilding the graph when the caller already has it (e.g., the wake-up drill).",
    )
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(
            f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)",
            file=sys.stderr,
        )
        return 2

    if args.init:
        return init_config(wiki_root, force=args.force, graph_json_arg=args.graph_json)

    report = evaluate(wiki_root, graph_json_arg=args.graph_json)
    print(json.dumps(report, indent=2, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
