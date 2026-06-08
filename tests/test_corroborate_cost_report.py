"""Unit tests for corroborate_cost_report.py — the fork-vs-inline verdict
helper. Pins the median + gate + fresh/fat filtering logic so the decision
rule can't silently drift.

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


def _batch(bid, growth=None, pre=None, total=None, flags=None, date="2026-06-08"):
    return {bid: {"batch_id": bid, "log": "corroborate", "date": date,
                  "context_growth": growth, "context_tokens_pre_pass": pre,
                  "total_tokens": total, "flags_in_batch": flags}}


class TestVerdict(unittest.TestCase):
    def test_insufficient_below_target_n(self):
        batches = {}
        batches.update(_batch("a", growth=40000, pre=10000, total=90000, flags=10))
        summary = rep.compute(batches, fresh_threshold=60000, target_n=5)
        # Only 1 growth sample though it crosses — n < target dominates.
        self.assertEqual(summary["verdict"], "INSUFFICIENT")
        self.assertEqual(summary["n_growth"], 1)

    def test_not_warranted_when_neither_median_crosses(self):
        batches = {}
        for i in range(5):
            batches.update(_batch(f"b{i}", growth=20000, pre=10000, total=50000, flags=10))
        summary = rep.compute(batches, fresh_threshold=60000, target_n=5)
        # median growth 20K < 30K; per-flag 5K < 8K -> NOT-WARRANTED.
        self.assertEqual(summary["verdict"], "NOT-WARRANTED")
        self.assertEqual(summary["median_context_growth"], 20000)
        self.assertEqual(summary["median_perflag_total_tokens"], 5000)

    def test_fork_warranted_via_growth(self):
        batches = {}
        for i in range(5):
            batches.update(_batch(f"c{i}", growth=35000, pre=10000, total=40000, flags=10))
        summary = rep.compute(batches, fresh_threshold=60000, target_n=5)
        self.assertEqual(summary["verdict"], "FORK-WARRANTED")
        self.assertTrue(summary["growth_crosses"])
        self.assertFalse(summary["perflag_crosses"])

    def test_fat_sessions_excluded_from_perflag_median(self):
        batches = {}
        # 5 growth-bearing batches; 2 fresh (pre<60K) with cheap per-flag,
        # 3 fat (pre>=60K) with expensive per-flag that must NOT count.
        for i in range(2):
            batches.update(_batch(f"fresh{i}", growth=10000, pre=20000, total=40000, flags=10))
        for i in range(3):
            batches.update(_batch(f"fat{i}", growth=10000, pre=180000, total=200000, flags=10))
        summary = rep.compute(batches, fresh_threshold=60000, target_n=5)
        self.assertEqual(summary["n_growth"], 5)
        self.assertEqual(summary["n_fresh_perflag"], 2)
        # Median over the 2 fresh per-flags (4000 each), not the 20000 fat ones.
        self.assertEqual(summary["median_perflag_total_tokens"], 4000)

    def test_null_growth_batches_dont_count(self):
        batches = {}
        batches.update(_batch("g", growth=35000, pre=10000, total=40000, flags=10))
        batches.update(_batch("h", growth=None, pre=None, total=None, flags=None))
        summary = rep.compute(batches, fresh_threshold=60000, target_n=5)
        self.assertEqual(summary["n_growth"], 1)  # null-growth batch excluded


class TestCollectAndMain(unittest.TestCase):
    def test_end_to_end_json(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = Path(d)
            (wiki / ".meta").mkdir()
            rows = [
                {"batch_id": "x-1", "timestamp": "2026-06-08T10:00:00",
                 "context_growth": 31000, "context_tokens_pre_pass": 15000,
                 "total_tokens": 100000, "flags_in_batch": 10},
                {"batch_id": "x-1", "timestamp": "2026-06-08T10:00:00",
                 "context_growth": 31000, "context_tokens_pre_pass": 15000,
                 "total_tokens": 100000, "flags_in_batch": 10},  # same batch, dedup
                {"batch_id": None, "timestamp": "2026-05-01T10:00:00"},  # pre-instrumentation
            ]
            path = wiki / ".meta" / "corroborate_log.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            batches = rep.collect_batches(wiki)
            self.assertEqual(len(batches), 1)  # deduped + null-batch skipped
            summary = rep.compute(batches, 60000, 5)
            self.assertEqual(summary["n_growth"], 1)
            self.assertEqual(summary["verdict"], "INSUFFICIENT")
            self.assertEqual(rep.main(["--wiki", str(wiki), "--json"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
