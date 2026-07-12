"""Unit tests for `trace_claim` — quote-level vault provenance (/wiki-trace A1).

Pins the two-tier match contract:
- VERBATIM: whitespace-normalized substring of a vault paragraph = exact
  provenance; wins outright over any fuzzy candidate.
- FUZZY: stdlib token-containment fallback, only when no verbatim span exists
  anywhere; surfaces paraphrase candidates, never presented as proof.
- FUZZY TIERS (trace v1.2): candidates are score-tiered — score >= strong-score
  (default 0.75) = `likely_verbatim_home` (a mangled-verbatim outlier: the claim
  probably IS extracted, reformatted past what normalize() folds), below =
  `topical_noise` (flat vocabulary overlap, consistent with a synthesized
  claim). Verbatim payloads carry no tier fields.
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


class NormalizeReformatTests(unittest.TestCase):
    """normalize() closes a class of verbatim-trace misses where the wiki compiler
    cosmetically reformats a directly-EXTRACTED claim: it backticks code spans,
    bolds labels, inserts `→` flow arrows, and rewrites number-words as digits in
    tables ("three" -> "3"). Because the same normalization runs on both the query
    and every vault paragraph, the EXTRACTED claim survives these edits and still
    resolves to its verbatim source span instead of dropping to the fuzzy tier
    (surfaced by the session-107 /wiki-trace demand-exercise, scenarios S2 + S4a)."""

    def test_strips_backticks(self):
        self.assertEqual(tc.normalize("the `-p` flag"), "the -p flag")

    def test_strips_bold_markers(self):
        self.assertEqual(tc.normalize("**Cost:** roughly free"), "cost: roughly free")

    def test_strips_flow_arrow(self):
        self.assertEqual(tc.normalize("GPT drafts → Claude audits"), "gpt drafts claude audits")

    def test_folds_number_word_to_digit(self):
        self.assertEqual(tc.normalize("flagged three real bugs"), "flagged 3 real bugs")
        self.assertEqual(tc.normalize("flagged 3 real bugs"), "flagged 3 real bugs")

    def test_folds_teens_and_tens(self):
        self.assertEqual(tc.normalize("nineteen and ninety"), "19 and 90")

    def test_number_fold_respects_word_boundaries(self):
        # 'one'/'ten' embedded in 'someone'/'zone'/'often' must NOT be folded.
        self.assertEqual(tc.normalize("someone in the zone often"), "someone in the zone often")

    def test_single_asterisk_preserved(self):
        # Only **bold** (double) is stripped; a lone '*' (glob, emphasis, math) is
        # kept so a query like '*.md' doesn't degenerate into a substring of every
        # '.md' file path in the vault.
        self.assertEqual(tc.normalize("files match *.md"), "files match *.md")


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

    def test_backtick_reformatted_claim_resolves_verbatim(self):
        """S4a: the wiki backticks a code span the raw source leaves plain; the
        claim must still resolve verbatim, not drop to fuzzy."""
        self._drop(
            "Web Articles/cost.md",
            "\n**Cost:** ~$0.02 per /codex:rescue call at GPT-5.4 pricing.\n",
        )
        r = tc.trace(self.vault, "~$0.02 per `/codex:rescue` call at GPT-5.4 pricing")
        self.assertEqual(r["match_type"], "verbatim")
        self.assertEqual(r["results"][0]["file"], "Web Articles/cost.md")

    def test_digit_word_reformatted_claim_resolves_verbatim(self):
        """S2: the wiki renders the count as '3', the raw source wrote 'three'; the
        claim must still resolve verbatim, not drop to fuzzy."""
        self._drop(
            "Web Articles/bugs.md",
            "\nAfter review, Codex flagged three real bugs in the code.\n",
        )
        r = tc.trace(self.vault, "Codex flagged 3 real bugs")
        self.assertEqual(r["match_type"], "verbatim")
        self.assertEqual(r["results"][0]["file"], "Web Articles/bugs.md")


class FuzzyTierTests(unittest.TestCase):
    """Trace v1.2: the fuzzy score-tier split. A high-containment outlier is a
    likely (reformatted) verbatim home; flat scores near the 0.5 floor are
    topical noise. Fixture scores are exact: the 8-content-word query scores
    1.0 against the reordered home and 0.5 against the topical paragraph."""

    QUERY = "agents rewrite themselves continuously under noisy production loads"
    # All 8 content words present, but reordered → fuzzy 1.0, not verbatim.
    STRONG_PARA = "Under noisy production loads, agents will continuously rewrite themselves.\n"
    # Exactly 4 of 8 content words present → fuzzy 0.5.
    TOPICAL_PARA = "Noisy agents are common in production, and loads of them fail.\n"

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

    def test_strong_candidate_tiered_likely_verbatim_home(self):
        self._drop("Web Articles/strong.md", "\n" + self.STRONG_PARA)
        r = tc.trace(self.vault, self.QUERY)
        self.assertEqual(r["match_type"], "fuzzy")
        self.assertEqual(r["results"][0]["score"], 1.0)
        self.assertEqual(r["results"][0]["tier"], "likely_verbatim_home")

    def test_flat_candidate_tiered_topical_noise(self):
        self._drop("Web Articles/topical.md", "\n" + self.TOPICAL_PARA)
        r = tc.trace(self.vault, self.QUERY)
        self.assertEqual(r["match_type"], "fuzzy")
        self.assertEqual(r["results"][0]["score"], 0.5)
        self.assertEqual(r["results"][0]["tier"], "topical_noise")

    def test_mixed_tiers_summary_and_order(self):
        """Strong outlier sorts first; the payload summarizes both tiers."""
        self._drop("Web Articles/strong.md", "\n" + self.STRONG_PARA)
        self._drop("Web Articles/topical.md", "\n" + self.TOPICAL_PARA)
        r = tc.trace(self.vault, self.QUERY)
        self.assertEqual(
            [x["tier"] for x in r["results"]],
            ["likely_verbatim_home", "topical_noise"],
        )
        self.assertEqual(
            r["fuzzy_tiers"], {"likely_verbatim_home": 1, "topical_noise": 1}
        )
        self.assertEqual(r["strong_score"], tc.STRONG_SCORE_DEFAULT)
        # Fuzzy result shape now includes the tier.
        for hit in r["results"]:
            self.assertEqual(
                set(hit), {"file", "slug", "line", "score", "paragraph", "tier"}
            )

    def test_boundary_score_is_strong_and_flag_reclassifies(self):
        """score == strong_score tiers strong; raising the bar demotes it."""
        # 3 of the query's 4 content words → exactly 0.75.
        self._drop("Web Articles/b.md", "\nThe harness beats the model.\n")
        query = "harness quality beats model"
        r = tc.trace(self.vault, query)
        self.assertEqual(r["results"][0]["score"], 0.75)
        self.assertEqual(r["results"][0]["tier"], "likely_verbatim_home")
        r2 = tc.trace(self.vault, query, strong_score=0.8)
        self.assertEqual(r2["results"][0]["tier"], "topical_noise")
        self.assertEqual(r2["strong_score"], 0.8)

    def test_verbatim_payload_carries_no_tier_fields(self):
        self._drop("Web Articles/v.md", "\nThe harness matters more than the model.\n")
        r = tc.trace(self.vault, "harness matters more than the model")
        self.assertEqual(r["match_type"], "verbatim")
        self.assertNotIn("fuzzy_tiers", r)
        self.assertNotIn("strong_score", r)
        self.assertNotIn("tier", r["results"][0])

    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = tc.main(argv)
        return rc, out.getvalue()

    def test_human_render_splits_sections(self):
        self._drop("Web Articles/strong.md", "\n" + self.STRONG_PARA)
        self._drop("Web Articles/topical.md", "\n" + self.TOPICAL_PARA)
        rc, out = self._run([str(self.vault), self.QUERY])
        self.assertEqual(rc, 0)
        self.assertIn("Likely reformatted verbatim home(s)", out)
        self.assertIn("Topical candidates (vocabulary overlap only", out)
        # The strong hit is listed before the topical one.
        self.assertLess(out.index("strong.md"), out.index("topical.md"))

    def test_human_render_topical_only_signals_synthesized(self):
        self._drop("Web Articles/topical.md", "\n" + self.TOPICAL_PARA)
        rc, out = self._run([str(self.vault), self.QUERY])
        self.assertEqual(rc, 0)
        self.assertIn("Topical candidates only", out)
        self.assertIn("synthesized", out)
        self.assertNotIn("Likely reformatted verbatim home(s)", out)

    def test_json_and_strong_score_flag(self):
        import json

        self._drop("Web Articles/b.md", "\nThe harness beats the model.\n")
        rc, out = self._run(
            [str(self.vault), "harness quality beats model", "--json",
             "--strong-score", "0.9"]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["match_type"], "fuzzy")
        self.assertEqual(payload["strong_score"], 0.9)
        self.assertEqual(payload["results"][0]["tier"], "topical_noise")
        self.assertEqual(
            payload["fuzzy_tiers"], {"likely_verbatim_home": 0, "topical_noise": 1}
        )


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
