"""Tests for emit_recent_ingests_skeleton.py --brief / --fill (s137, script audit Wave 3).

The curator no longer opens overview.md: it reads the brief and writes a fill
JSON. apply_fill enforces the Mode A scope rule in code: only the placeholder
tokens in the window's section change, and bad fills change nothing.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

from __future__ import annotations

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

import emit_recent_ingests_skeleton as sk  # noqa: E402

LOG = """# Log

## [2026-11-01 10:00] ingest | Alpha Source
- Created: pages/sources/2026-11-01-alpha.md, pages/concepts/alpha-concept.md
- Updated: pages/concepts/agents.md

## [2026-11-02 10:00] ingest | Beta Source
- Created: pages/sources/2026-11-02-beta.md
"""

OVERVIEW = """# Overview

Intro paragraph that must never change.

## Current state

State prose.

### Sub-thread

## Ingests 0–0 — Earlier batch

- old bullet

## Stats

- 3 pages
"""


class TestBriefFill(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki = Path(self._tmp.name)
        for d in ("sources", "concepts", "entities"):
            (self.wiki / "pages" / d).mkdir(parents=True)
        (self.wiki / "log.md").write_text(LOG, encoding="utf-8")
        (self.wiki / "pages/sources/2026-11-01-alpha.md").write_text(
            '---\ntitle: "Alpha Source"\nsummary: "Alpha argues for X."\ntags: [agents, x]\n---\n', encoding="utf-8")
        (self.wiki / "pages/sources/2026-11-02-beta.md").write_text(
            '---\ntitle: "Beta Source"\nsummary: "Beta measures Y."\n---\n', encoding="utf-8")
        (self.wiki / "pages/concepts/agents.md").write_text("---\ntitle: Agents\n---\n", encoding="utf-8")
        self.overview = self.wiki / "overview.md"
        self.overview.write_text(OVERVIEW, encoding="utf-8")
        section = sk.build_section(self.wiki, 1, 2)
        _, text = sk.inject(self.overview, section, 1, 2)
        self.overview.write_text(text, encoding="utf-8")
        self.before = text

    def tearDown(self):
        self._tmp.cleanup()

    def test_brief_has_outline_skeleton_and_summaries(self):
        brief = sk.build_brief(self.wiki, 1, 2)
        self.assertIn("- Current state", brief)
        self.assertIn("  Sub-thread", brief)
        self.assertIn("## Ingests 0–0 — Earlier batch", brief)
        self.assertIn(sk.SECTION_PLACEHOLDER, brief)
        self.assertIn("summary: Alpha argues for X.", brief)
        self.assertIn("tags: agents, x", brief)
        self.assertNotIn("Intro paragraph", brief)  # the brief is not the overview

    def test_fill_touches_only_placeholders(self):
        fill = {"section_title": "Two sources", "synthesis": {"1": "anchors [[agents]].", "2": "measures Y"}}
        new, problems = sk.apply_fill(self.wiki, 1, 2, fill)
        self.assertEqual(problems, [])
        self.assertIn("## Ingests 1–2 — Two sources", new)
        self.assertIn("anchors [[agents]].", new)
        self.assertNotIn(sk.SYNTHESIS_PLACEHOLDER, new)
        # Every byte outside the placeholders is unchanged.
        expected = (self.before.replace(sk.SECTION_PLACEHOLDER, "Two sources", 1)
                    .replace(sk.SYNTHESIS_PLACEHOLDER, "anchors [[agents]]", 1)
                    .replace(sk.SYNTHESIS_PLACEHOLDER, "measures Y", 1))
        self.assertEqual(new, expected)

    def test_bad_fills_refused(self):
        cases = [
            ({"section_title": "T", "synthesis": {"1": "a"}}, "missing ingests: 2"),
            ({"section_title": "T", "synthesis": {"1": "a", "2": "b", "3": "c"}}, "outside 1–2"),
            ({"section_title": "", "synthesis": {"1": "a", "2": "b"}}, "section_title is empty"),
            ({"section_title": "T", "synthesis": {"1": "a\nb", "2": "b"}}, "one line"),
            ({"section_title": "T", "synthesis": {"1": "see [[nope]]", "2": "b"}}, "[[nope]] does not resolve"),
        ]
        for fill, msg in cases:
            new, problems = sk.apply_fill(self.wiki, 1, 2, fill)
            self.assertTrue(any(msg in p for p in problems), (msg, problems))
            self.assertEqual(new, self.before)

    def test_refill_refused(self):
        fill = {"section_title": "T", "synthesis": {"1": "a", "2": "b"}}
        new, _ = sk.apply_fill(self.wiki, 1, 2, fill)
        self.overview.write_text(new, encoding="utf-8")
        _, problems = sk.apply_fill(self.wiki, 1, 2, fill)
        self.assertTrue(any("already filled" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
