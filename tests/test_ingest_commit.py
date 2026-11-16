"""Tests for `ingest_commit.py` — the ingestor's one-call close (script audit #8, s142).

Builds a throwaway git repo (project root with `vault/` + `wiki/`) and pins:
created/updated are derived from git, not typed; only the listed paths (plus
index outputs and the raw source) land in the commit, so an unrelated staged
change stays out; the log entry carries the real hash; bad input commits
nothing.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import shutil
import subprocess
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


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))
import ingest_commit  # noqa: E402

PAGE = """---
title: "{title}"
type: {typ}
category: test
summary: "A page."
confidence: 0.6
sources:
  - vault/Clippings/2026-11-16-src.md
last_updated: 2026-11-16
---
# {title}
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", stdin=subprocess.DEVNULL, check=True).stdout


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class IngestCommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "wiki"
        (self.wiki / "pages" / "concepts").mkdir(parents=True)
        (self.wiki / "pages" / "sources").mkdir(parents=True)
        (self.wiki / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (self.wiki / "log.md").write_text("# Wiki Log\n\n", encoding="utf-8")
        (self.root / "vault" / "Clippings").mkdir(parents=True)
        self.src = self.root / "vault" / "Clippings" / "2026-11-16-src.md"
        self.src.write_text("raw\n", encoding="utf-8")
        self._page("concepts", "old-concept", "Old")
        (self.root / "unrelated.txt").write_text("a\n", encoding="utf-8")
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "T")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def _page(self, typ, slug, title):
        p = self.wiki / "pages" / typ / f"{slug}.md"
        p.write_text(PAGE.format(title=title, typ=typ.rstrip("s")), encoding="utf-8")
        return p

    def run_commit(self, *extra):
        return ingest_commit.main([str(self.wiki), "--source", "vault/Clippings/2026-11-16-src.md",
                                   "--title", "Test Source", "--no-lint", *extra])

    def test_derives_lists_and_commits_only_listed_paths(self):
        self._page("sources", "2026-11-16-src", "Src")
        old = self.wiki / "pages" / "concepts" / "old-concept.md"
        old.write_text(old.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
        (self.root / "unrelated.txt").write_text("b\n", encoding="utf-8")
        git(self.root, "add", "unrelated.txt")  # the user's own staged change
        rc = self.run_commit("--paths", "pages/sources/2026-11-16-src.md", "pages/concepts/old-concept.md",
                             "--trailer", "Co-Authored-By: Bot <b@example.com>")
        self.assertEqual(rc, 0)
        files = git(self.root, "show", "--name-status", "--format=", "HEAD").split("\n")
        self.assertIn("A\twiki/pages/sources/2026-11-16-src.md", files)
        self.assertIn("M\twiki/pages/concepts/old-concept.md", files)
        self.assertFalse(any("unrelated.txt" in f for f in files))
        self.assertIn("unrelated.txt", git(self.root, "diff", "--cached", "--name-only"))  # still staged
        msg = git(self.root, "log", "-1", "--format=%B")
        self.assertTrue(msg.startswith("ingest: Test Source"))
        self.assertIn("- Created: 2026-11-16-src", msg)
        self.assertIn("- Updated: old-concept", msg)
        self.assertIn("Co-Authored-By: Bot <b@example.com>", msg)
        head = git(self.root, "rev-parse", "--short", "HEAD").strip()
        log = (self.wiki / "log.md").read_text(encoding="utf-8")
        self.assertIn("ingest | Test Source", log)
        self.assertIn(f"- Commit: {head}", log)
        self.assertIn("- Created: 2026-11-16-src", log)
        # index outputs were generated and committed alongside
        self.assertTrue(any("wiki/pages/indexes/" in f for f in files))

    def test_comma_list_and_wiki_prefix_accepted(self):
        self._page("concepts", "new-a", "A")
        self._page("concepts", "new-b", "B")
        rc = self.run_commit("--paths", "wiki/pages/concepts/new-a.md,pages/concepts/new-b.md")
        self.assertEqual(rc, 0)
        self.assertIn("- Created: new-a, new-b", git(self.root, "log", "-1", "--format=%B"))

    def test_bad_path_commits_nothing(self):
        before = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(self.run_commit("--paths", "pages/concepts/missing.md"), 1)
        self.assertEqual(self.run_commit("--paths", "log.md"), 1)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), before)

    def test_unchanged_pages_commit_nothing(self):
        # commit the index outputs first, so a regen produces no diff
        ingest_commit.script("update_index.py", str(self.wiki))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "index")
        before = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(self.run_commit("--paths", "pages/concepts/old-concept.md"), 1)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), before)

    def test_bad_decision_value_rejected(self):
        self._page("concepts", "new-a", "A")
        self.assertEqual(self.run_commit("--paths", "pages/concepts/new-a.md", "--verify-decision", "new-a=X"), 1)


if __name__ == "__main__":
    unittest.main()
