"""Unit tests for `pre_screen` tag-less handling and `triage_backlog` matching (s147).

Since 2026-11-05 the user clips with the Obsidian Chrome extension: no topical
tags in frontmatter, raw-title filenames. Two regressions pinned here:

- pre_screen: a tag-less clip is NOT warned for missing tags (it was, so every
  new clip came back `warn`); a source that HAS tags with zero vocabulary
  overlap still warns.
- triage_backlog: pending files come from the canonical hash+stem matcher, so
  a vault file renamed after ingest (hash matches a source page, stem doesn't)
  is not pending. The stem-only copy counted 26 where the drill counted 19.
  Records carry the `canonical_slug` normalize_vault would give the file.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (
        here.parents[2] / "wiki-forge" / "scripts",
        here.parents[1] / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


sys.path.insert(0, str(_scripts_dir()))
from pre_screen import pre_screen  # noqa: E402
from triage_backlog import triage  # noqa: E402

BODY = ("Claude Code agents use hooks, skills, memory and the context window "
        "to run an agent harness over a codebase. ") * 20


def _clip(title: str, tags: str | None = None, body: str = BODY) -> str:
    fm = [f'title: "{title}"', 'url: "https://medium.com/@x/y-123abc"',
          'author:\n  - "[[Some Author]]"', "published: 2026-10-22"]
    if tags is not None:
        fm.append(f"tags: [{tags}]")
    return "---\n" + "\n".join(fm) + "\n---\n" + body


class _Repo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.wiki, self.vault = root / "wiki", root / "vault"
        (self.wiki / "pages" / "sources").mkdir(parents=True)
        (self.vault / "Clippings").mkdir(parents=True)
        (self.vault / ".skipped").mkdir()
        (self.wiki / "pages" / "indexes").mkdir()
        (self.wiki / "pages" / "indexes" / "index-concepts.md").write_text(
            "tags: [claude-code, hooks, agent-harness, memory, context-window]\n", encoding="utf-8")

    def clip(self, name: str, text: str) -> Path:
        p = self.vault / "Clippings" / name
        p.write_bytes(text.encode("utf-8"))
        return p

    def source(self, slug: str, source_hash: str | None = None):
        fm = "---\ntype: source\n" + (f"source_hash: {source_hash}\n" if source_hash else "") + "---\nbody\n"
        (self.wiki / "pages" / "sources" / f"{slug}.md").write_text(fm, encoding="utf-8")


class TestPreScreenTagless(unittest.TestCase):
    def setUp(self):
        self.r = _Repo()

    def tearDown(self):
        self.r.tmp.cleanup()

    def test_tagless_clip_passes(self):
        p = self.r.clip("2026-10-22-My Second Brain.md", _clip("My Second Brain"))
        res = pre_screen(self.r.wiki, p, self.r.vault)
        self.assertEqual(res["verdict"], "pass", res["reasons"])
        self.assertTrue(res["tagless"])
        self.assertFalse(any("No tags" in r for r in res["reasons"]))

    def test_tagged_without_overlap_still_warns(self):
        p = self.r.clip("2026-10-22-x.md", _clip("X", tags="gardening, cooking"))
        res = pre_screen(self.r.wiki, p, self.r.vault)
        self.assertEqual(res["verdict"], "warn")
        self.assertFalse(res["tagless"])

    def test_tagless_promo_still_warns(self):
        p = self.r.clip("2026-10-22-y.md", _clip("Y", body=BODY + " Use code SAVE20 for a discount."))
        res = pre_screen(self.r.wiki, p, self.r.vault)
        self.assertEqual(res["verdict"], "warn")


class TestTriageBacklogMatcher(unittest.TestCase):
    def setUp(self):
        self.r = _Repo()

    def tearDown(self):
        self.r.tmp.cleanup()

    def test_renamed_after_ingest_is_not_pending(self):
        text = _clip("Claude Code Memory")
        self.r.clip("2026-05-24-claude-code-memory-the-five-layers-that-fix.md", text)
        self.r.source("2026-05-24-claude-code-memory",
                      hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
        rep = triage(self.r.wiki, self.r.vault)
        self.assertEqual(rep["pending"], 0)
        self.assertEqual(rep["already_processed"], 1)

    def test_pending_carries_canonical_slug(self):
        self.r.clip("2026-10-22-My Second Brain Has a Cockpit Now. Claude Does the Driving.md",
                    _clip("My Second Brain Has a Cockpit Now. Claude Does the Driving."))
        rep = triage(self.r.wiki, self.r.vault)
        self.assertEqual(rep["pending"], 1)
        self.assertEqual(rep["records"][0]["canonical_slug"],
                         "2026-10-22-my-second-brain-has-a-cockpit-now-claude-does-the-driving")

    def test_skip_stub_and_stem_match_handled(self):
        self.r.clip("2026-01-01-a.md", _clip("A"))
        self.r.clip("2026-01-02-b.md", _clip("B"))
        self.r.source("2026-01-01-a")
        (self.r.vault / ".skipped" / "2026-01-02-b.md").write_text("---\nreason: shallow\n---\n", encoding="utf-8")
        self.assertEqual(triage(self.r.wiki, self.r.vault)["pending"], 0)


if __name__ == "__main__":
    unittest.main()
