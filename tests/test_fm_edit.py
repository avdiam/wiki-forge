"""Tests for `fm_edit.py` — line-preserving frontmatter edits (script audit #8, s142).

Pins: untouched lines keep their exact text; block and flow lists grow in their
own style; `contradicts:` edges are written and removed symmetrically (the
asymmetric-edge lint class); verify-clearing drops the flag and bumps
confidence one tier; a bad slug writes nothing.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

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
import fm_edit  # noqa: E402
from frontmatter import parse_frontmatter  # noqa: E402

FOWLER = """---
title: "Martin Fowler"
type: entity
category: ai-practitioners
summary: "Writer."
tags: [agent-harness, refactoring]
aliases: [fowler]
confidence: 0.7
sources:
  - vault/Web Articles/2026-04-21-a.md
status: active
last_updated: 2026-06-05
verify: true
stale: false
---

# Martin Fowler

Body with ⚠ needs verification: kept.
"""

BOCKELER = """---
title: "Birgitta Böckeler"
type: entity
category: ai-practitioners
summary: "Engineer."
tags: [agent-harness]
confidence: 0.6
sources: [vault/Web Articles/2026-06-05-b.md]
contradicts: ["[[martin-fowler]]", "[[other]]"]
last_updated: 2026-06-05
---
Body.
"""

OTHER = """---
title: "Other"
type: concept
confidence: 0.5
sources:
  - vault/x.md
contradicts: ["[[birgitta-bockeler]]"]
last_updated: 2026-01-01
---
"""


class FmEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wiki = Path(self.tmp.name)
        for t in fm_edit.PAGE_TYPES:
            (self.wiki / "pages" / t).mkdir(parents=True)
        self.fowler = self._page("entities", "martin-fowler", FOWLER)
        self.bock = self._page("entities", "birgitta-bockeler", BOCKELER)
        self.other = self._page("concepts", "other", OTHER)

    def tearDown(self):
        self.tmp.cleanup()

    def _page(self, typ, slug, text):
        p = self.wiki / "pages" / typ / f"{slug}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def run_edit(self, *args):
        return fm_edit.main([str(self.wiki), *args])

    def fm(self, p):
        return parse_frontmatter(p.read_text(encoding="utf-8"))

    def test_shared_ops_across_pages_preserve_other_lines(self):
        rc = self.run_edit("martin-fowler", "pages/entities/birgitta-bockeler.md",
                           "--add-source", "vault/new.md", "--touch", "--date", "2026-11-16")
        self.assertEqual(rc, 0)
        f = self.fowler.read_text(encoding="utf-8")
        self.assertIn("sources:\n  - vault/Web Articles/2026-04-21-a.md\n  - vault/new.md\nstatus: active", f)
        self.assertIn("last_updated: 2026-11-16", f)
        self.assertEqual(f.replace("  - vault/new.md\n", "").replace("2026-11-16", "2026-06-05"), FOWLER)
        self.assertEqual(self.fm(self.bock)["sources"], ["vault/Web Articles/2026-06-05-b.md", "vault/new.md"])

    def test_add_source_is_idempotent(self):
        self.run_edit("martin-fowler", "--add-source", "vault/Web Articles/2026-04-21-a.md")
        self.assertEqual(self.fowler.read_text(encoding="utf-8"), FOWLER)

    def test_tags_aliases_dedupe_case_insensitive(self):
        self.run_edit("martin-fowler", "--add-tags", "Refactoring,thoughtworks", "--add-aliases", "m-fowler")
        fm = self.fm(self.fowler)
        self.assertEqual(fm["tags"], ["agent-harness", "refactoring", "thoughtworks"])
        self.assertEqual(fm["aliases"], ["fowler", "m-fowler"])

    def test_aliases_created_when_absent(self):
        self.run_edit("birgitta-bockeler", "--add-aliases", "bockeler")
        self.assertEqual(self.fm(self.bock)["aliases"], ["bockeler"])

    def test_confidence_set_and_delta_clamped(self):
        self.run_edit("martin-fowler", "--confidence", "+0.1")
        self.assertEqual(self.fm(self.fowler)["confidence"], 0.8)
        self.run_edit("martin-fowler", "--confidence", "+0.5")
        self.assertIn("confidence: 1.0\n", self.fowler.read_text(encoding="utf-8"))
        self.run_edit("martin-fowler", "--confidence", "0.45")
        self.assertEqual(self.fm(self.fowler)["confidence"], 0.45)

    def test_contradicts_is_symmetric(self):
        self.run_edit("martin-fowler", "--contradicts", "other", "--touch", "--date", "2026-11-16")
        self.assertEqual(self.fm(self.fowler)["contradicts"], ["[[other]]"])
        self.assertEqual(self.fm(self.other)["contradicts"], ["[[birgitta-bockeler]]", "[[martin-fowler]]"])
        # the inverse side is a content change too, so it gets touched
        self.assertEqual(str(self.fm(self.other)["last_updated"]), "2026-11-16")
        self.assertIn('contradicts: ["[[birgitta-bockeler]]", "[[martin-fowler]]"]',
                      self.other.read_text(encoding="utf-8"))

    def test_uncontradict_removes_both_sides_and_drops_empty(self):
        self.run_edit("birgitta-bockeler", "--uncontradict", "other")
        self.assertEqual(self.fm(self.bock)["contradicts"], ["[[martin-fowler]]"])
        self.assertNotIn("contradicts:", self.other.read_text(encoding="utf-8"))

    def test_clear_verify_bumps_confidence_and_keeps_body(self):
        self.run_edit("martin-fowler", "--clear-verify")
        text = self.fowler.read_text(encoding="utf-8")
        self.assertNotIn("verify: true", text)
        self.assertIn("⚠ needs verification: kept.", text)
        self.assertEqual(self.fm(self.fowler)["confidence"], 0.8)

    def test_set_scalar(self):
        self.run_edit("martin-fowler", "--set", "status=deprecated")
        self.assertEqual(self.fm(self.fowler)["status"], "deprecated")

    def test_bad_slug_writes_nothing(self):
        rc = self.run_edit("martin-fowler", "--touch", "--contradicts", "no-such-page")
        self.assertEqual(rc, 1)
        self.assertEqual(self.fowler.read_text(encoding="utf-8"), FOWLER)

    def test_self_contradiction_rejected(self):
        self.assertEqual(self.run_edit("martin-fowler", "--contradicts", "martin-fowler"), 1)
        self.assertEqual(self.fowler.read_text(encoding="utf-8"), FOWLER)

    def test_crlf_preserved(self):
        self.fowler.write_bytes(FOWLER.replace("\n", "\r\n").encode("utf-8"))
        self.run_edit("martin-fowler", "--set", "status=deprecated")
        raw = self.fowler.read_bytes().decode("utf-8")
        self.assertNotIn("\n", raw.replace("\r\n", ""))
        self.assertIn("status: deprecated\r\n", raw)


if __name__ == "__main__":
    unittest.main()
