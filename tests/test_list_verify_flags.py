"""Unit tests for `list_verify_flags.py` age filtering (`--older-than`, `--as-of`)
and `verify_queue_summary()` — the wake-up drill's `verify_queue` field
(script-audit event tail, s143).

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import list_verify_flags as lvf  # noqa: E402  (run_tests.py puts the scripts dir on sys.path)

AS_OF = date(2026, 11, 16)


def _page(wiki: Path, sub: str, slug: str, *, verify=True, last_updated="2026-11-01",
          ptype=None, category="tools"):
    d = wiki / "pages" / sub
    d.mkdir(parents=True, exist_ok=True)
    types = {"entities": "entity", "concepts": "concept", "comparisons": "comparison", "syntheses": "synthesis"}
    fm = [f"type: {ptype or types[sub]}", f"category: {category}",
          f"verify: {'true' if verify else 'false'}", "sources:", "  - \"[[s1]]\"", "confidence: 0.5"]
    if last_updated is not None:
        fm.append(f"last_updated: {last_updated}")
    body = "\n⚠ needs verification: claim about " + slug + "\n"
    (d / f"{slug}.md").write_text("---\n" + "\n".join(fm) + "\n---\n" + body, encoding="utf-8")


def _wiki(tmp: str) -> Path:
    wiki = Path(tmp) / "wiki"
    wiki.mkdir()
    (wiki / "CLAUDE.md").write_text("x", encoding="utf-8")
    _page(wiki, "entities", "old-bio", last_updated="2026-04-28", category="ai-practitioners")  # 202d, A
    _page(wiki, "entities", "mid-tool", last_updated="2026-09-17")                             # 60d, B (boundary)
    _page(wiki, "concepts", "fresh-idea", last_updated="2026-11-10")                           # 6d, C
    _page(wiki, "concepts", "undated", last_updated=None)                                      # never aged
    _page(wiki, "concepts", "cleared", verify=False, last_updated="2026-01-01")                # not in queue
    return wiki


def _cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = lvf.main(argv)
    return rc, buf.getvalue()


class TestAgeDays(unittest.TestCase):
    def test_parse_forms(self):
        self.assertEqual(lvf.age_days("2026-11-06", AS_OF), 10)
        self.assertEqual(lvf.age_days(date(2026, 11, 6), AS_OF), 10)
        self.assertEqual(lvf.age_days("2026-11-06T12:00:00", AS_OF), 10)
        self.assertIsNone(lvf.age_days(None, AS_OF))
        self.assertIsNone(lvf.age_days("not-a-date", AS_OF))


class TestOlderThan(unittest.TestCase):
    def test_filter_boundary_inclusive_and_undated_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = lvf._read_pages(_wiki(tmp))
            self.assertEqual(len(pages), 4)  # verify:false page excluded
            slugs = [p["page_slug"] for p in lvf.filter_older_than(pages, 60, AS_OF)]
            self.assertEqual(slugs, ["old-bio", "mid-tool"])  # 60d counts; oldest first
            self.assertEqual(lvf.filter_older_than(pages, 0, AS_OF)[-1]["page_slug"], "fresh-idea")

    def test_cli_total_only_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = _wiki(tmp)
            rc, out = _cli(["--wiki", str(wiki), "--older-than", "60", "--as-of", "2026-11-16", "--total-only"])
            self.assertEqual((rc, out.strip()), (0, "2"))
            rc, out = _cli(["--wiki", str(wiki), "--older-than", "180", "--as-of", "2026-11-16", "--all"])
            j = json.loads(out)
            self.assertEqual((j["total"], j["returned"], j["queue_total"]), (1, 1, 4))
            self.assertEqual((j["older_than_days"], j["as_of"]), (180, "2026-11-16"))
            self.assertEqual(j["flags"][0]["page_slug"], "old-bio")
            self.assertEqual(j["buckets_total"], {"A": 1, "B": 0, "C": 0})

    def test_cli_without_age_flag_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = _cli(["--wiki", str(_wiki(tmp)), "--all"])
            j = json.loads(out)
            self.assertEqual(j["total"], 4)
            self.assertNotIn("older_than_days", j)
            self.assertNotIn("queue_total", j)

    def test_cli_rejects_bad_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = _wiki(tmp)
            with self.assertRaises(SystemExit):
                _cli(["--wiki", str(wiki), "--older-than", "-1"])
            with self.assertRaises(SystemExit):
                _cli(["--wiki", str(wiki), "--older-than", "5", "--as-of", "16/11/2026"])


class TestSummary(unittest.TestCase):
    def test_summary_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = lvf.verify_queue_summary(_wiki(tmp), older_than=60, as_of=AS_OF)
            self.assertEqual(s, {"total": 4, "aged": 2, "older_than_days": 60, "oldest_days": 202,
                                 "oldest_slug": "old-bio", "aged_buckets": {"A": 1, "B": 1, "C": 0}})

    def test_summary_empty_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki = Path(tmp) / "wiki"
            wiki.mkdir()
            s = lvf.verify_queue_summary(wiki, as_of=AS_OF)
            self.assertEqual((s["total"], s["aged"], s["oldest_days"], s["oldest_slug"]), (0, 0, None, None))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "wiki-forge" / "scripts"))
    unittest.main()
