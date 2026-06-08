#!/usr/bin/env python3
"""Compute the fork-vs-inline decision verdict for the `/wiki-corroborate` +
`/wiki-resolve-contradictions` execution-shape question (dev/tech-debt.md
"execution shape — inline vs subagent" entry).

Pools the per-invocation cost telemetry from `corroborate_log.jsonl` and
`contradiction_log.jsonl` (the two skills share the cost machinery and the entry
says their telemetry is pooled), dedups to one record per `batch_id`, and applies
the **2026-06-08 de-confounded gate**:

  - **Context discipline** — median per-invocation `context_growth` (= post − pre)
    over ALL growth-bearing batches. Threshold: ≥ 30K. `context_growth` is a
    delta, so it is valid from any session regardless of prior fill.
  - **Throughput** — median per-flag `total_tokens / flags_in_batch` over
    FRESH-session batches only. Threshold: ≥ 8K. `total_tokens` includes
    `cache_creation`, which inflates with cache churn in a fat session, so a
    batch only counts toward this median if it started in a roughly-fresh
    session. Freshness proxy: `context_tokens_pre_pass < --fresh-threshold`
    (default 60K — the session was not already loaded when the pass began).

Verdict:
  - **INSUFFICIENT** — fewer than `--target-n` growth-bearing batches; keep draining.
  - **FORK-WARRANTED** — n ≥ target AND at least one median crosses its gate →
    build `.claude/agents/wiki-corroborator.md` and run the fork arm to compare.
  - **NOT-WARRANTED** — n ≥ target AND neither median crosses → close the entry;
    inline is fine.

Accrual is passive: every `/wiki-corroborate` / `/wiki-resolve-contradictions`
run banks a batch automatically. Just drain the verify queue normally and re-run
this report.

Stdlib only; Python 3.9-safe.

Usage:
    python corroborate_cost_report.py --wiki wiki
    python corroborate_cost_report.py --wiki wiki --json
    python corroborate_cost_report.py --wiki wiki --fresh-threshold 50000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median

GROWTH_GATE = 30_000
PERFLAG_GATE = 8_000
FRESH_PRE_THRESHOLD = 60_000
TARGET_N = 5
LOGS = ("corroborate_log.jsonl", "contradiction_log.jsonl")


def _read_rows(path: Path) -> list:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def collect_batches(wiki: Path) -> dict:
    """One record per batch_id, carrying the invocation-level cost fields (which
    are identical across every row of a batch). Rows without a batch_id predate
    instrumentation and are skipped."""
    batches: dict = {}
    for name in LOGS:
        for r in _read_rows(wiki / ".meta" / name):
            bid = r.get("batch_id")
            if not bid or bid in batches:
                continue
            batches[bid] = {
                "batch_id": bid,
                "log": name.split("_")[0],
                "date": (r.get("timestamp") or "")[:10],
                "context_growth": r.get("context_growth"),
                "context_tokens_pre_pass": r.get("context_tokens_pre_pass"),
                "total_tokens": r.get("total_tokens"),
                "flags_in_batch": r.get("flags_in_batch"),
            }
    return batches


def compute(batches: dict, fresh_threshold: int, target_n: int) -> dict:
    growth = [
        b["context_growth"] for b in batches.values()
        if b.get("context_growth") is not None
    ]
    per_flag_fresh = []
    for b in batches.values():
        pre = b.get("context_tokens_pre_pass")
        tot = b.get("total_tokens")
        flags = b.get("flags_in_batch")
        if tot is None or not flags:
            continue
        if pre is not None and pre < fresh_threshold:  # fresh-session only
            per_flag_fresh.append(tot / flags)

    n = len(growth)
    med_growth = median(growth) if growth else None
    med_perflag = median(per_flag_fresh) if per_flag_fresh else None

    growth_crosses = med_growth is not None and med_growth >= GROWTH_GATE
    perflag_crosses = med_perflag is not None and med_perflag >= PERFLAG_GATE

    if n < target_n:
        verdict = "INSUFFICIENT"
    elif growth_crosses or perflag_crosses:
        verdict = "FORK-WARRANTED"
    else:
        verdict = "NOT-WARRANTED"

    return {
        "n_growth": n,
        "target_n": target_n,
        "median_context_growth": med_growth,
        "growth_gate": GROWTH_GATE,
        "growth_crosses": growth_crosses,
        "n_fresh_perflag": len(per_flag_fresh),
        "median_perflag_total_tokens": med_perflag,
        "perflag_gate": PERFLAG_GATE,
        "perflag_crosses": perflag_crosses,
        "fresh_threshold": fresh_threshold,
        "verdict": verdict,
    }


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


def render(summary: dict, batches: dict) -> str:
    lines = []
    lines.append("=== /wiki-corroborate execution-shape cost report ===")
    lines.append(
        f"VERDICT: {summary['verdict']}   "
        f"(n={summary['n_growth']}/{summary['target_n']} growth-bearing batches)"
    )
    lines.append("")
    gx = "CROSSES" if summary["growth_crosses"] else "below"
    px = "CROSSES" if summary["perflag_crosses"] else "below"
    lines.append(
        f"  median context_growth        = {_fmt(summary['median_context_growth'])}"
        f"   (gate {summary['growth_gate']:,} — {gx})"
    )
    lines.append(
        f"  median per-flag total_tokens = {_fmt(summary['median_perflag_total_tokens'])}"
        f"   (gate {summary['perflag_gate']:,} — {px}; "
        f"n_fresh={summary['n_fresh_perflag']}, pre<{summary['fresh_threshold']:,})"
    )
    lines.append("")
    lines.append("  batches:")
    for b in sorted(batches.values(), key=lambda x: x["date"]):
        pre = b.get("context_tokens_pre_pass")
        fresh = "fresh" if (pre is not None and pre < summary["fresh_threshold"]) else "fat"
        flags = b.get("flags_in_batch")
        tot = b.get("total_tokens")
        perflag = (tot / flags) if (tot and flags) else None
        lines.append(
            f"    [{b['date']}] {b['batch_id']:<16} {b['log']:<13} "
            f"growth={_fmt(b.get('context_growth')):>8}  "
            f"per-flag={_fmt(perflag):>8} ({fresh}, flags={flags})"
        )
    if summary["verdict"] == "INSUFFICIENT":
        need = summary["target_n"] - summary["n_growth"]
        lines.append("")
        lines.append(
            f"  -> keep draining: {need} more growth-bearing batch(es) to reach n={summary['target_n']}. "
            "Every /wiki-corroborate or /wiki-resolve-contradictions run banks one."
        )
    return "\n".join(lines)


def parse_args(argv: list) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    p.add_argument("--fresh-threshold", type=int, default=FRESH_PRE_THRESHOLD,
                   help="context_tokens_pre_pass below this counts as a fresh session "
                        f"for the per-flag total_tokens median (default {FRESH_PRE_THRESHOLD}).")
    p.add_argument("--target-n", type=int, default=TARGET_N,
                   help=f"growth-bearing batches needed before a verdict fires (default {TARGET_N}).")
    p.add_argument("--json", action="store_true", help="Emit the summary as JSON.")
    return p.parse_args(argv)


def main(argv: list) -> int:
    args = parse_args(argv)
    wiki = Path(args.wiki).expanduser().resolve()
    if not (wiki / ".meta").is_dir():
        print(f"error: {wiki} has no .meta/ — not a wiki root", file=sys.stderr)
        return 2
    batches = collect_batches(wiki)
    summary = compute(batches, args.fresh_threshold, args.target_n)
    if args.json:
        print(json.dumps(summary))
    else:
        print(render(summary, batches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
