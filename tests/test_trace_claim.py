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


class ExtractClaimSentenceTests(unittest.TestCase):
    """A2: pull the claim sentence ending at a `{#claim-id}` anchor."""

    def test_takes_last_sentence(self):
        self.assertEqual(
            tc.extract_claim_sentence("Some intro. The claim here."),
            "The claim here.",
        )

    def test_strips_trailing_tag_marker(self):
        self.assertEqual(
            tc.extract_claim_sentence("The synthesized claim. *[INFERRED]* "),
            "The synthesized claim.",
        )

    def test_strips_multiple_tag_markers(self):
        self.assertEqual(
            tc.extract_claim_sentence("A contested claim. *[AMBIGUOUS]* *[INFERRED]*"),
            "A contested claim.",
        )

    def test_strips_leading_blockquote(self):
        self.assertEqual(
            tc.extract_claim_sentence("> A blockquoted claim."),
            "A blockquoted claim.",
        )

    def test_strips_leading_bullet(self):
        self.assertEqual(
            tc.extract_claim_sentence("- A bulleted claim."),
            "A bulleted claim.",
        )

    def test_keeps_bold_intact(self):
        # `**Bold` must not be mistaken for a `* ` list bullet (needs whitespace).
        self.assertEqual(
            tc.extract_claim_sentence("**Bold** claim stands."),
            "**Bold** claim stands.",
        )

    def test_single_sentence_passthrough(self):
        self.assertEqual(tc.extract_claim_sentence("Just one claim."), "Just one claim.")


class ResolveClaimAnchorTests(unittest.TestCase):
    """A2: resolve `[[page-slug#claim-id]]` to the claim text on the wiki page."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki = Path(self._tmp.name)
        (self.wiki / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        page_dir = self.wiki / "pages" / "entities"
        page_dir.mkdir(parents=True)
        (page_dir / "foo.md").write_text(
            "---\ntitle: Foo\n---\n\n"
            "Intro sentence. The harness matters more than the model. "
            "*[INFERRED]* {#claim-harness}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_resolves_claim_text_and_line(self):
        res = tc.resolve_claim_anchor(self.wiki, "foo", "claim-harness")
        self.assertTrue(res["ok"])
        self.assertEqual(res["claim_text"], "The harness matters more than the model.")
        self.assertEqual(res["page"], "pages/entities/foo.md")
        self.assertEqual(res["line"], 5)

    def test_page_not_found(self):
        res = tc.resolve_claim_anchor(self.wiki, "nope", "claim-harness")
        self.assertFalse(res["ok"])
        self.assertIn("page not found", res["error"])

    def test_anchor_not_found(self):
        res = tc.resolve_claim_anchor(self.wiki, "foo", "claim-missing")
        self.assertFalse(res["ok"])
        self.assertIn("not found", res["error"])


class AnchorQueryRegexTests(unittest.TestCase):
    def test_matches_anchor_form(self):
        m = tc.ANCHOR_QUERY_RE.match("[[claude-code#claim-cc-launch]]")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "claude-code")
        self.assertEqual(m.group(2), "claim-cc-launch")

    def test_tolerates_inner_whitespace(self):
        m = tc.ANCHOR_QUERY_RE.match("[[ foo # claim-bar ]]")
        self.assertIsNotNone(m)
        self.assertEqual((m.group(1), m.group(2)), ("foo", "claim-bar"))

    def test_plain_quote_is_not_an_anchor(self):
        self.assertIsNone(tc.ANCHOR_QUERY_RE.match("the harness matters more than the model"))

    def test_non_claim_anchor_rejected(self):
        # `#heading` without the `claim-` prefix is not a claim anchor.
        self.assertIsNone(tc.ANCHOR_QUERY_RE.match("[[foo#some-heading]]"))


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
        self.assertEqual(payload["input_kind"], "quote")


class AnchorCliTests(unittest.TestCase):
    """A2 end-to-end: a `[[page#claim-id]]` query resolves on the wiki and
    traces the claim text into the vault."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = root / "vault"
        self.wiki = root / "wiki"
        (self.vault / "Web Articles").mkdir(parents=True)
        (self.vault / "Web Articles" / "src.md").write_text(
            "\nThe harness matters more than the model.\n", encoding="utf-8"
        )
        self.wiki.mkdir(parents=True)
        (self.wiki / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        page_dir = self.wiki / "pages" / "entities"
        page_dir.mkdir(parents=True)
        (page_dir / "foo.md").write_text(
            "---\ntitle: Foo\n---\n\n"
            "Intro. The harness matters more than the model. {#claim-harness}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = tc.main(argv)
        return rc, out.getvalue()

    def test_anchor_resolves_and_traces_with_wiki_flag(self):
        rc, out = self._run(
            [str(self.vault), "[[foo#claim-harness]]", "--wiki", str(self.wiki)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("Resolved [[foo#claim-harness]]", out)
        self.assertIn("pages/entities/foo.md", out)
        self.assertIn("Verbatim source", out)

    def test_anchor_sibling_wiki_inference(self):
        # No --wiki: the wiki is the sibling 'wiki' dir of the vault root.
        rc, out = self._run([str(self.vault), "[[foo#claim-harness]]"])
        self.assertEqual(rc, 0)
        self.assertIn("Verbatim source", out)

    def test_anchor_json_shape(self):
        import json

        rc, out = self._run(
            [str(self.vault), "[[foo#claim-harness]]", "--wiki", str(self.wiki), "--json"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["input_kind"], "anchor")
        self.assertEqual(payload["anchor"], {"page_slug": "foo", "claim_id": "claim-harness"})
        self.assertEqual(payload["resolved"]["page"], "pages/entities/foo.md")
        self.assertEqual(
            payload["resolved"]["claim_text"], "The harness matters more than the model."
        )
        self.assertEqual(payload["match_type"], "verbatim")

    def test_anchor_page_not_found_exits_2(self):
        rc, _ = self._run(
            [str(self.vault), "[[missing#claim-x]]", "--wiki", str(self.wiki)]
        )
        self.assertEqual(rc, 2)

    def test_anchor_not_found_exits_2(self):
        rc, _ = self._run(
            [str(self.vault), "[[foo#claim-absent]]", "--wiki", str(self.wiki)]
        )
        self.assertEqual(rc, 2)

    def test_anchor_missing_wiki_root_exits_2(self):
        rc, _ = self._run(
            [str(self.vault), "[[foo#claim-harness]]", "--wiki", str(self.wiki / "nope")]
        )
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
