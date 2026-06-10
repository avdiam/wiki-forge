"""Unit tests for corroborate_cost_report.py — the fork-vs-inline verdict
helper. Pins the median + gate logic so the decision rule can't silently drift.

2026-06-10: the throughput gate moved off `total_tokens / flags` (fresh-only) to
cache-immune `output_tokens_pass / flags` (all batches). These tests pin the new
shape: output is cache-immune, so there is no fresh/fat split for it, and the
small-n_fresh fragility is gone.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path


def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (
        here.parents[2] / "wiki" / ".scripts",
        here.parents[1] / "skills" / "wiki-init" / "templates" / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))

import corroborate_cost_report as rep


def _batch(bid, growth=None, pre=None, output=None, flags=None, date="2026-06-08"):
    return {bid: {"batch_id": bid, "log": "corroborate", "date": date,
                  "context_growth": growth, "context_tokens_pre_pass": pre,
                  "output_tokens_pass": output, "flags_in_batch": flags}}


class TestVerdict(unittest.TestCase):
    def test_insufficient_below_target_n(self):
        batches = {}
        batches.update(_batch("a", growth=40000, output=90000, flags=10))
        summary = rep.compute(batches, target_n=5)
        # Only 1 growth sample though it crosses — n < target dominates.
        self.assertEqual(summary["verdict"], "INSUFFICIENT")
        self.assertEqual(summary["n_growth"], 1)

    def test_not_warranted_when_neither_median_crosses(self):
        batches = {}
        for i in range(5):
            batches.update(_batch(f"b{i}", growth=20000, output=40000, flags=10))
        summary = rep.compute(batches, target_n=5)
        # median growth 20K < 30K; per-flag output 4K < 5K -> NOT-WARRANTED.
        self.assertEqual(summary["verdict"], "NOT-WARRANTED")
        self.assertEqual(summary["median_context_growth"], 20000)
        self.assertEqual(summary["median_perflag_output_tokens"], 4000)

    def test_fork_warranted_via_growth(self):
        batches = {}
        for i in range(5):
            batches.update(_batch(f"c{i}", growth=35000, output=40000, flags=10))
        summary = rep.compute(batches, target_n=5)
        self.assertEqual(summary["verdict"], "FORK-WARRANTED")
        self.assertTrue(summary["growth_crosses"])
        self.assertFalse(summary["output_crosses"])

    def test_fork_warranted_via_output(self):
        batches = {}
        # growth below gate, but per-flag output 6K >= 5K gate fires.
        for i in range(5):
            batches.update(_batch(f"d{i}", growth=10000, output=60000, flags=10))
        summary = rep.compute(batches, target_n=5)
        self.assertEqual(summary["verdict"], "FORK-WARRANTED")
        self.assertFalse(summary["growth_crosses"])
        self.assertTrue(summary["output_crosses"])
        self.assertEqual(summary["median_perflag_output_tokens"], 6000)

    def test_output_median_counts_all_sessions_regardless_of_warmth(self):
        batches = {}
        # 2 fresh-ish (low pre) + 3 fat (high pre); output is cache-immune so
        # ALL 5 count toward the output median — no fresh/fat split.
        for i in range(2):
            batches.update(_batch(f"fresh{i}", growth=10000, pre=20000, output=40000, flags=10))
        for i in range(3):
            batches.update(_batch(f"fat{i}", growth=10000, pre=180000, output=40000, flags=10))
        summary = rep.compute(batches, target_n=5)
        self.assertEqual(summary["n_growth"], 5)
        self.assertEqual(summary["n_output"], 5)  # all 5, not just the fresh ones
        self.assertEqual(summary["median_perflag_output_tokens"], 4000)

    def test_output_counts_even_when_growth_is_null(self):
        batches = {}
        # A batch can have output+flags but null growth (post not captured) —
        # it must still count toward the output median, not the growth median.
        batches.update(_batch("e", growth=None, pre=None, output=50000, flags=10))
        summary = rep.compute(batches, target_n=5)
        self.assertEqual(summary["n_growth"], 0)
        self.assertEqual(summary["n_output"], 1)
        self.assertEqual(summary["median_perflag_output_tokens"], 5000)

    def test_null_growth_batches_dont_count(self):
        batches = {}
        batches.update(_batch("g", growth=35000, output=40000, flags=10))
        batches.update(_batch("h", growth=None, pre=None, output=None, flags=None))
        summary = rep.compute(batches, target_n=5)
        self.assertEqual(summary["n_growth"], 1)  # null-growth batch excluded


class TestCollectAndMain(unittest.TestCase):
    def test_end_to_end_json(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = Path(d)
            (wiki / ".meta").mkdir()
            rows = [
                {"batch_id": "x-1", "timestamp": "2026-06-08T10:00:00",
                 "context_growth": 31000, "context_tokens_pre_pass": 15000,
                 "output_tokens_pass": 40000, "flags_in_batch": 10},
                {"batch_id": "x-1", "timestamp": "2026-06-08T10:00:00",
                 "context_growth": 31000, "context_tokens_pre_pass": 15000,
                 "output_tokens_pass": 40000, "flags_in_batch": 10},  # same batch, dedup
                {"batch_id": None, "timestamp": "2026-05-01T10:00:00"},  # pre-instrumentation
            ]
            path = wiki / ".meta" / "corroborate_log.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            batches = rep.collect_batches(wiki)
            self.assertEqual(len(batches), 1)  # deduped + null-batch skipped
            summary = rep.compute(batches, 5)
            self.assertEqual(summary["n_growth"], 1)
            self.assertEqual(summary["verdict"], "INSUFFICIENT")
            self.assertEqual(rep.main(["--wiki", str(wiki), "--json"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
