"""Tests for lint_wiki.py's compact agent output (s135 script audit, Wave 1).

- check_orphans ignores self-links (a page linking to itself is still an orphan).
- count_of / report_counts give a fixed key set with int counts.
- summarize_report truncates long lists, reports cadence, honours --only.
- --log-mechanical writes one lint_log.jsonl row with real fields.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

here = Path(__file__).resolve()


def _scripts_dir() -> Path:
    for cand in (
        here.parents[2] / "wiki-forge" / "scripts",
        here.parents[1] / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


sys.path.insert(0, str(_scripts_dir()))

import lint_wiki as lw  # noqa: E402

PAGE = """---
title: {title}
type: concept
category: testing
summary: A test page.
confidence: 0.9
sources: [vault/x.md]
last_updated: 2026-11-01
---

{body}
"""


def _make_wiki(root: Path, pages: dict[str, str], log: str = "") -> Path:
    wiki = root / "wiki"
    (wiki / "pages" / "concepts").mkdir(parents=True)
    (wiki / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (wiki / ".meta").mkdir()
    (wiki / "log.md").write_text(log, encoding="utf-8")
    (wiki / "CATEGORIES.md").write_text(
        "## Authoritative category list\n\n- `testing` — test pages\n", encoding="utf-8")
    (root / "vault").mkdir()
    for slug, body in pages.items():
        (wiki / "pages" / "concepts" / f"{slug}.md").write_text(
            PAGE.format(title=slug, body=body), encoding="utf-8")
    return wiki


class TestOrphans(unittest.TestCase):

    def test_self_link_is_not_inbound(self):
        pages = {
            "a": {"body": "see [[a]] and [[b]]"},
            "b": {"body": "nothing"},
        }
        self.assertEqual(lw.check_orphans(pages), ["a"])


class TestCounts(unittest.TestCase):

    def test_count_of_shapes(self):
        self.assertEqual(lw.count_of([1, 2, 3]), 3)
        self.assertEqual(lw.count_of(None), 0)
        self.assertEqual(lw.count_of({"missing_index": False, "in_pages_not_index": ["x"],
                                      "in_index_not_pages": []}), 1)
        self.assertEqual(lw.count_of({"missing_index": True, "in_pages_not_index": [],
                                      "in_index_not_pages": []}), 1)
        self.assertEqual(lw.count_of({"marker_ingest": 3, "lag": 7}), 1)

    def test_report_counts_skips_meta(self):
        rep = {"wiki_root": "w", "vault_dir": "v", "total_pages": 9, "checked_at": "t",
               "orphans": ["a"], "stale": []}
        self.assertEqual(lw.report_counts(rep), {"orphans": 1, "stale": 0})


class TestSummaryAndLog(unittest.TestCase):

    def test_summary_truncates_and_cadence(self):
        with tempfile.TemporaryDirectory() as d:
            log = "".join(f"## [2026-11-0{i}] ingest | s{i}\n" for i in range(1, 4))
            wiki = _make_wiki(Path(d), {f"p{i}": "no links" for i in range(8)}, log)
            pages = lw.collect_pages(wiki)
            rep = lw.build_report(wiki, None, pages)
            out = lw.summarize_report(rep, wiki, pages, detail=3)
            self.assertEqual(out["counts"]["orphans"], 8)
            self.assertEqual(len(out["details"]["orphans"]), 3)
            self.assertEqual(out["truncated"]["orphans"], 5)
            self.assertEqual(out["ingest_count"], 3)
            self.assertEqual(out["lint_counter"], 3)
            self.assertFalse(out["full_lint_due"])
            self.assertEqual(out["verdict"], "issues")
            self.assertEqual(out["category_report"]["counts"], {"testing": 8})

            only = lw.summarize_report(rep, wiki, pages, detail=3, only=["orphans", "nope"])
            self.assertEqual(list(only["details"]), ["orphans"])
            self.assertEqual(len(only["details"]["orphans"]), 8)  # --only = full detail
            self.assertEqual(only["unknown_checks"], ["nope"])
            self.assertNotIn("category_report", only)

    def test_log_mechanical_row(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = _make_wiki(Path(d), {"a": "[[b]]", "b": "[[a]]"},
                              "## [2026-11-01] ingest | s1\n")
            rc = lw.main([str(wiki), "--log-mechanical", "--commit", "abc1234"])
            self.assertEqual(rc, 0)
            rows = [json.loads(l) for l in (wiki / ".meta" / "lint_log.jsonl").read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["lint_type"], "mechanical")
            self.assertEqual(r["total_pages"], 2)
            self.assertEqual(r["ingest_count_at_run"], 1)
            self.assertEqual(r["commit_hash"], "abc1234")
            self.assertIsInstance(r["duration_ms"], int)
            self.assertIn("orphans", r["counts"])
            self.assertEqual(set(r["counts"]), set(lw.report_counts(
                lw.build_report(wiki, None)).keys()))


if __name__ == "__main__":
    unittest.main()
