"""Tests for `classify_candidates.py` — corroborate/resolve per-item classify,
format and log (script audit #9, s142).

Pins: denylist always dropped, tier_3 dropped unless asked, tier ordering;
"already in wiki" detection through the cited vault file's `url:`; the
coverage gate fires only above 60% unclassified; the telemetry row carries
the post-filter counts and URLs; `close` stamps the probe's cost fields onto
the batch.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (here.parents[2] / "wiki-forge" / "scripts", here.parents[1] / "scripts"):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


sys.path.insert(0, str(_scripts_dir()))
import classify_candidates as cc  # noqa: E402

ENTITY = """---
title: "{title}"
type: entity
category: tools
summary: "x"
confidence: 0.6
sources:
  - vault/Clippings/2026-01-01-src.md
contradicts: ["[[{other}]]"]
last_updated: {date}
verify: true
---
# {title}

⚠ needs verification: the claim about {title} needs a primary source.
"""

TIERS = {"tier_1": ["vendor.example"], "tier_2": ["pub.example"],
         "tier_3": ["forum.example"], "denylist": ["spam.example"]}


class ClassifyCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.wiki = root / "wiki"
        (self.wiki / "pages" / "entities").mkdir(parents=True)
        (self.wiki / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (root / "vault" / "Clippings").mkdir(parents=True)
        (root / "vault" / "Clippings" / "2026-01-01-src.md").write_text(
            "---\nurl: https://www.vendor.example/docs/page/\n---\nraw\n", encoding="utf-8")
        for slug, other, date in (("alpha", "beta", "2026-01-01"), ("beta", "alpha", "2026-02-01")):
            (self.wiki / "pages" / "entities" / f"{slug}.md").write_text(
                ENTITY.format(title=slug, other=other, date=date), encoding="utf-8")
        self._orig = cc._load_credibility_lists
        cc._load_credibility_lists = lambda: TIERS

    def tearDown(self):
        cc._load_credibility_lists = self._orig
        self.tmp.cleanup()

    def call(self, *args, stdin=""):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cc.main([str(self.wiki), *args], stdin_text=stdin)
        return rc, buf.getvalue()

    def rows(self, name):
        p = self.wiki / ".meta" / name
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    CANDS = ("https://unknown.example/a Unknown one\n"
             "https://forum.example/t/1 Forum thread\n"
             "https://spam.example/x Spam\n"
             "https://pub.example/review A review\n"
             "https://vendor.example/docs/page The cited source\n")

    def test_filter_order_and_already_cited(self):
        rc, out = self.call("corroborate", "--slug", "alpha", "--index", "2/5", "--no-log", stdin=self.CANDS)
        self.assertEqual(rc, 0)
        self.assertNotIn("spam.example", out)
        self.assertNotIn("forum.example", out)
        order = [out.index(u) for u in ("vendor.example", "pub.example", "unknown.example")]
        self.assertEqual(order, sorted(order))
        self.assertIn("(already in wiki — this is the source we're trying to corroborate)", out)
        self.assertIn("[2/5] wiki/pages/entities/alpha.md", out)
        self.assertIn("bucket: B", out)
        self.assertNotIn("Classifier coverage gap", out)
        self.assertEqual(self.rows("corroborate_log.jsonl"), [])

    def test_include_tier_3_and_log_row(self):
        rc, out = self.call("corroborate", "--slug", "alpha", "--include-tier-3", "--batch-id", "abcd1234-10",
                            stdin=self.CANDS)
        self.assertEqual(rc, 0, out)
        self.assertIn("[tier-3] https://forum.example/t/1", out)
        row = self.rows("corroborate_log.jsonl")[-1]
        self.assertEqual(row["candidates_returned"], 4)
        self.assertEqual(row["candidates_by_tier"], {"tier_1": 1, "tier_2": 1, "tier_3": 1, "unclassified": 1})
        self.assertEqual(row["batch_id"], "abcd1234-10")
        self.assertEqual(row["user_action"], "deferred")
        self.assertTrue(row["include_tier_3_flag"])

    def test_coverage_gate_above_threshold_only(self):
        _, out = self.call("corroborate", "--slug", "alpha", "--no-log",
                           stdin="https://u1.example/a A\nhttps://u2.example/b B\nhttps://vendor.example/c C\n")
        self.assertIn("Classifier coverage gap: 2 of 3", out)  # 0.67 > 0.60
        _, out = self.call("corroborate", "--slug", "alpha", "--no-log",
                           stdin="https://u1.example/a A\nhttps://vendor.example/c C\n")
        self.assertNotIn("Classifier coverage gap", out)  # 0.50

    def test_zero_candidates_still_logs(self):
        rc, out = self.call("corroborate", "--slug", "alpha")
        self.assertEqual(rc, 0)
        self.assertIn("no candidates above tier-3", out)
        self.assertEqual(self.rows("corroborate_log.jsonl")[-1]["candidates_returned"], 0)

    def test_resolve_block_and_row(self):
        rc, out = self.call("resolve", "--pair", "beta|alpha", "--index", "1/1", stdin=self.CANDS)
        self.assertEqual(rc, 0, out)
        self.assertIn("↕ contradicts ↕", out)
        self.assertIn("(already in wiki — cited by side A | B)", out)
        self.assertIn("[both_stand]", out)
        row = self.rows("contradiction_log.jsonl")[-1]
        self.assertEqual(row["pair_key"], "alpha|beta")
        self.assertEqual(row["candidates_returned"], 3)

    def test_unknown_slug_and_bad_line(self):
        self.assertEqual(self.call("corroborate", "--slug", "nope", "--no-log")[0], 1)
        self.assertEqual(self.call("corroborate", "--slug", "alpha", "--no-log", stdin="not a url\n")[0], 1)

    def test_close_stamps_cost(self):
        self.call("corroborate", "--slug", "alpha", "--batch-id", "abcd1234-0", stdin=self.CANDS)
        transcript = Path(self.tmp.name) / "t.jsonl"
        turn = {"type": "assistant", "timestamp": "2026-11-16T10:00:00Z",
                "message": {"usage": {"input_tokens": 10, "output_tokens": 5,
                                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                            "content": []}}
        transcript.write_text(json.dumps(turn) + "\n", encoding="utf-8")
        rc, out = self.call("close", "corroborate", "--batch-id", "abcd1234-0", "--session-file", str(transcript))
        self.assertEqual(rc, 0, out)
        row = self.rows("corroborate_log.jsonl")[-1]
        self.assertEqual(row.get("flags_in_batch"), 1)
        self.assertIsNotNone(row.get("total_tokens"))

    def test_close_rejects_bad_batch_id(self):
        self.assertEqual(self.call("close", "resolve", "--batch-id", "nodigits")[0], 1)


if __name__ == "__main__":
    unittest.main()
