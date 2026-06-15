#!/usr/bin/env python3
"""Compute the fork-vs-inline decision verdict for the `/wiki-corroborate` +
`/wiki-resolve-contradictions` execution-shape question (dev/tech-debt.md
"execution shape — inline vs subagent" entry).

Pools the per-invocation cost telemetry from `corroborate_log.jsonl` and
`contradiction_log.jsonl` (the two skills share the cost machinery and the entry
says their telemetry is pooled), dedups to one record per `batch_id`, and applies
the gate:

  - **Context discipline (PRIMARY, load-bearing)** — median per-invocation
    `context_growth` (= post − pre) over ALL growth-bearing batches.
    Threshold: ≥ 30K. `context_growth` is a delta, so it is valid from any
    session regardless of prior fill. This is the metric that actually tracks
    what a fork buys: a forked surface-pass keeps the WebSearch results +
    tier-classification reasoning out of the main session's resident context.

  - **Throughput (SECONDARY)** — median per-flag `output_tokens_pass /
    flags_in_batch` over ALL batches carrying both fields. Threshold: ≥ 5K.

    **2026-06-10 metric fix.** This gate previously keyed on
    `total_tokens / flags_in_batch` restricted to "fresh" sessions
    (`context_tokens_pre_pass < 60K`). That was wrong: `total_tokens` includes
    cache tokens, which inflate in BOTH directions — `cache_read` in a fat
    session (the bug the fresh-restriction tried to dodge) AND `cache_creation`
    in a fresh session (the unguarded direction: a fresh session *pays to build*
    the cache). So the "fresh-session per-flag total_tokens" the old gate trusted
    was itself contaminated, and with n_fresh small it could be set by a single
    cache-creation-inflated batch. `output_tokens_pass` (generated, never cached)
    is cache-immune in both directions, so it needs no fresh/fat split — every
    batch with output + flags counts, which also removes the small-n_fresh
    fragility. The 5K threshold is provisional (the old 8K was on a different,
    larger-scale metric); revisit if the distribution shifts. Note this signal
    is partly redundant with `context_growth` (output tokens are a large
    component of growth) — context_growth remains the load-bearing gate.

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
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median

GROWTH_GATE = 30_000
OUTPUT_PERFLAG_GATE = 5_000  # provisional — cache-immune throughput proxy (see docstring)
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
                "output_tokens_pass": r.get("output_tokens_pass"),
                "flags_in_batch": r.get("flags_in_batch"),
            }
    return batches


def compute(batches: dict, target_n: int) -> dict:
    growth = [
        b["context_growth"] for b in batches.values()
        if b.get("context_growth") is not None
    ]
    # Cache-immune throughput: output tokens are generated, never cached, so a
    # batch counts regardless of how warm its session was. No fresh/fat split.
    per_flag_output = []
    for b in batches.values():
        out = b.get("output_tokens_pass")
        flags = b.get("flags_in_batch")
        if out is None or not flags:
            continue
        per_flag_output.append(out / flags)

    n = len(growth)
    med_growth = median(growth) if growth else None
    med_output = median(per_flag_output) if per_flag_output else None

    growth_crosses = med_growth is not None and med_growth >= GROWTH_GATE
    output_crosses = med_output is not None and med_output >= OUTPUT_PERFLAG_GATE

    if n < target_n:
        verdict = "INSUFFICIENT"
    elif growth_crosses or output_crosses:
        verdict = "FORK-WARRANTED"
    else:
        verdict = "NOT-WARRANTED"

    return {
        "n_growth": n,
        "target_n": target_n,
        "median_context_growth": med_growth,
        "growth_gate": GROWTH_GATE,
        "growth_crosses": growth_crosses,
        "n_output": len(per_flag_output),
        "median_perflag_output_tokens": med_output,
        "output_gate": OUTPUT_PERFLAG_GATE,
        "output_crosses": output_crosses,
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
    ox = "CROSSES" if summary["output_crosses"] else "below"
    lines.append(
        f"  median context_growth         = {_fmt(summary['median_context_growth'])}"
        f"   (gate {summary['growth_gate']:,} — {gx})   [PRIMARY]"
    )
    lines.append(
        f"  median per-flag output_tokens = {_fmt(summary['median_perflag_output_tokens'])}"
        f"   (gate {summary['output_gate']:,} — {ox}; n={summary['n_output']})   [secondary]"
    )
    lines.append("")
    lines.append("  batches:")
    for b in sorted(batches.values(), key=lambda x: x["date"]):
        flags = b.get("flags_in_batch")
        out = b.get("output_tokens_pass")
        perflag = (out / flags) if (out and flags) else None
        lines.append(
            f"    [{b['date']}] {b['batch_id']:<16} {b['log']:<13} "
            f"growth={_fmt(b.get('context_growth')):>8}  "
            f"out/flag={_fmt(perflag):>8} (flags={flags})"
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
    summary = compute(batches, args.target_n)
    if args.json:
        print(json.dumps(summary))
    else:
        print(render(summary, batches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
