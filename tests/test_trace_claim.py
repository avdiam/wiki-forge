"""Unit tests for `trace_claim` — quote-level vault provenance (/wiki-trace A1).

Pins the two-tier match contract:
- VERBATIM: whitespace-normalized substring of a vault paragraph = exact
  provenance; wins outright over any fuzzy candidate.
- FUZZY: stdlib token-containment fallback, only when no verbatim span exists
  anywhere; surfaces paraphrase candidates, never presented as proof.
- EXCLUSION: files under `.`/`_` first-segment paths are never searched (the
  canonical ingest-exclusion rule — a verbatim string hiding in `.skipped/`
  must not surface).
- Paragraph line numbers point at the matched span so the result is locatable.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


# Locate the helper-scripts dir in either the live (dev/tests/ -> wiki-forge/scripts)
# or the wiki-forge (tests/ -> scripts) layout, so this file stays byte-identical
# across both repos.
def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (
        here.parents[2] / "wiki-forge" / "scripts",
        here.parents[1] / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))
import trace_claim as tc


class PureHelperTests(unittest.TestCase):
    def test_normalize_collapses_whitespace(self):
        self.assertEqual(tc.normalize("  The\tHarness\n  Matters "), "the harness matters")

    def test_content_words_drops_short_tokens(self):
        # `to`/`of`/`an`/`ai` (len < 3) dropped; hyphen splits; lowercased.
        self.assertEqual(
            tc.content_words("To the Agent-Harness, of an AI!"),
            {"the", "agent", "harness"},
        )

    def test_content_words_concrete(self):
        self.assertEqual(tc.content_words("of an AI to"), set())  # all len < 3
        self.assertEqual(tc.content_words("model harness 2026"), {"model", "harness", "2026"})

    def test_iter_paragraphs_line_numbers(self):
        text = "---\ntitle: X\n---\n\nFirst para line five.\n\nSecond para line seven.\n"
        paras = list(tc.iter_paragraphs(text))
        self.assertEqual(paras[0], (1, "---\ntitle: X\n---"))
        self.assertEqual(paras[1], (5, "First para line five."))
        self.assertEqual(paras[2], (7, "Second para line seven."))

    def test_is_excluded(self):
        self.assertTrue(tc.is_excluded(Path(".skipped/x.md")))
        self.assertTrue(tc.is_excluded(Path("_mailbox/note.md")))
        self.assertFalse(tc.is_excluded(Path("Web Articles/x.md")))
        self.assertFalse(tc.is_excluded(Path("Clippings/y.md")))


class TraceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / "Web Articles").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _drop(self, relpath: str, text: str) -> None:
        p = self.vault / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_verbatim_match(self):
        self._drop(
            "Web Articles/2026-01-01-harness.md",
            "---\ntitle: Harness\n---\n\nThe harness matters more than the model in 2026.\n",
        )
        r = tc.trace(self.vault, "harness matters more than the model")
        self.assertEqual(r["match_type"], "verbatim")
        self.assertEqual(r["result_count"], 1)
        hit = r["results"][0]
        self.assertEqual(hit["file"], "Web Articles/2026-01-01-harness.md")
        self.assertEqual(hit["slug"], "2026-01-01-harness")
        self.assertEqual(hit["line"], 5)
        self.assertIn("harness matters more than the model", hit["paragraph"])

    def test_verbatim_normalizes_whitespace(self):
        """A quote with different internal whitespace still matches verbatim."""
        self._drop(
            "Web Articles/a.md",
            "---\n---\n\nThe harness matters more than the model in 2026.\n",
        )
        r = tc.trace(self.vault, "harness matters\nmore   than\tthe model")
        self.assertEqual(r["match_type"], "verbatim")
        self.assertEqual(r["result_count"], 1)

    def test_verbatim_wins_over_fuzzy(self):
        """When any verbatim span exists, fuzzy candidates are suppressed."""
        # Verbatim home (sorts first by path).
        self._drop("Web Articles/a-exact.md", "\nThe harness matters more than the model.\n")
        # A paragraph that would fuzzy-match the same query but is not verbatim.
        self._drop("Web Articles/b-para.md", "\nMatters of harness and model, the more the better.\n")
        r = tc.trace(self.vault, "harness matters more than the model")
        self.assertEqual(r["match_type"], "verbatim")
        self.assertEqual([h["file"] for h in r["results"]], ["Web Articles/a-exact.md"])

    def test_fuzzy_fallback(self):
        """No verbatim span anywhere → closest paraphrase candidate by token overlap."""
        self._drop(
            "Web Articles/2026-01-02-agents.md",
            "---\n---\n\nIn production, agents will rewrite themselves over time.\n",
        )
        r = tc.trace(self.vault, "agents rewrite themselves in production")
        self.assertEqual(r["match_type"], "fuzzy")
        self.assertEqual(r["result_count"], 1)
        hit = r["results"][0]
        self.assertEqual(hit["file"], "Web Articles/2026-01-02-agents.md")
        self.assertGreaterEqual(hit["score"], 0.5)
        self.assertIn("line", hit)

    def test_min_score_filters_fuzzy(self):
        """Below-threshold token overlap is not surfaced; raising the bar drops it."""
        self._drop("Web Articles/x.md", "\nAgents and harnesses and models and tokens galore.\n")
        # Only 1 of 4 query content-words present -> score 0.25 < default 0.5.
        r = tc.trace(self.vault, "agents rewrite themselves production")
        self.assertEqual(r["match_type"], "none")

    def test_excluded_paths_not_searched(self):
        """A verbatim string under a `.`/`_` segment must not surface."""
        self._drop(".skipped/secret.md", "\nThe harness matters more than the model.\n")
        self._drop("_mailbox/note.md", "\nThe harness matters more than the model.\n")
        r = tc.trace(self.vault, "harness matters more than the model")
        self.assertEqual(r["match_type"], "none")
        # The excluded files are not even counted in files_searched.
        self.assertEqual(r["files_searched"], 0)

    def test_no_match(self):
        self._drop("Web Articles/a.md", "\nWholly unrelated content about gardening.\n")
        r = tc.trace(self.vault, "quantum chromodynamics lattice gauge")
        self.assertEqual(r["match_type"], "none")
        self.assertEqual(r["results"], [])

    def test_result_shape_and_cap(self):
        for i in range(7):
            self._drop(f"Web Articles/f{i}.md", "\nThe shared verbatim claim appears here.\n")
        r = tc.trace(self.vault, "shared verbatim claim", max_results=3)
        self.assertEqual(r["match_type"], "verbatim")
        self.assertEqual(r["result_count"], 3)  # capped
        for hit in r["results"]:
            self.assertEqual(set(hit), {"file", "slug", "line", "paragraph"})
        self.assertIn("duration_ms", r)
        self.assertEqual(r["files_searched"], 7)


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / "Web Articles").mkdir()
        (self.vault / "Web Articles" / "a.md").write_text(
            "\nThe harness matters more than the model.\n", encoding="utf-8"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = tc.main(argv)
        return rc, out.getvalue()

    def test_bad_vault_root(self):
        rc, _ = self._run([str(self.vault / "nope"), "anything"])
        self.assertEqual(rc, 2)

    def test_empty_query(self):
        rc, _ = self._run([str(self.vault), '  ""  '])
        self.assertEqual(rc, 2)

    def test_human_ok(self):
        rc, out = self._run([str(self.vault), "harness matters more than the model"])
        self.assertEqual(rc, 0)
        self.assertIn("Verbatim source", out)
        self.assertIn("Web Articles/a.md", out)

    def test_json_ok(self):
        rc, out = self._run([str(self.vault), "harness matters", "--json"])
        self.assertEqual(rc, 0)
        import json

        payload = json.loads(out)
        self.assertEqual(payload["match_type"], "verbatim")
        self.assertEqual(payload["query"], "harness matters")


if __name__ == "__main__":
    unittest.main()
