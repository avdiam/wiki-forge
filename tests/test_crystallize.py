"""Unit tests for `crystallize` — standalone-fact extraction merge planner (v1).

Pins the load-bearing contract:
- PROVENANCE is trace-determined: a verbatim vault hit ⇒ EXTRACTED (untagged) +
  `sources:` pinned to that source; no verbatim home ⇒ `*[INFERRED]*` + the
  synthesis page's underlying sources.
- S5 GUARD: a coincidental verbatim hit in a file the synthesis does NOT cite is
  not adopted as provenance (intersected with the synthesis page's `sources:`).
- DEDUP: a fact already stated on the home page ⇒ `already_present`, never merged.
- CLAIM-ID uniqueness: a minted `{#claim-id}` never collides with the target
  page's existing anchors, nor with another fact merged to the same page this run.
- MERGE-ONLY routing: a fact with no/unknown home ⇒ `unhomed`, never a merge.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

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


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))
import crystallize as cz


def _page(path, ptype, fm, body):
    return {"path": path, "type": ptype, "frontmatter": fm, "body": body}


class SlugifyTests(unittest.TestCase):
    def test_keys_off_content_words(self):
        cid = cz.slugify_claim("The widget ships in March 2026 to all tiers.", set())
        self.assertTrue(cid.startswith("claim-"))
        # stopwords ("the", "in", "to", "all") dropped; content words kept
        self.assertIn("widget", cid)
        self.assertNotIn("the", cid.split("-"))

    def test_disambiguates_against_taken(self):
        taken = {"claim-widget-ships-march-2026"}
        first = cz.slugify_claim("Widget ships March 2026", taken)
        # base collides → suffixed
        self.assertNotIn(first, taken)
        self.assertTrue(first.endswith("-2"))

    def test_empty_fact_falls_back(self):
        self.assertEqual(cz.slugify_claim("a an the", set()), "claim-fact")


class PresenceTests(unittest.TestCase):
    def test_normalized_substring_caught(self):
        body = "# P\n\n- The model ships **three** variants. {#claim-x}\n"
        # number-word fold + bold strip ⇒ "3" restatement is detected
        self.assertTrue(cz.is_present("the model ships 3 variants", body))

    def test_absent_fact_not_present(self):
        self.assertFalse(cz.is_present("totally unrelated claim", "# P\n\nother text\n"))


class AttributeFactTests(unittest.TestCase):
    def test_verbatim_extracted_pins_cited_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Web Articles").mkdir(parents=True)
            (vault / "Web Articles" / "src.md").write_text(
                "Intro.\n\nThe widget ships in March 2026 to all tiers.\n",
                encoding="utf-8",
            )
            syn_fm = {"sources": ["vault/Web Articles/src.md", "vault/Web Articles/other.md"]}
            attr = cz.attribute_fact(
                "The widget ships in March 2026 to all tiers.", vault, syn_fm
            )
            self.assertEqual(attr["provenance"], "verbatim")
            self.assertEqual(attr["tag"], "")  # untagged = implicit EXTRACTED
            self.assertEqual(attr["sources"], ["vault/Web Articles/src.md"])

    def test_s5_guard_excludes_uncited_verbatim_hit(self):
        # The fact appears verbatim in an UNCITED file; provenance must not adopt it.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Web Articles").mkdir(parents=True)
            (vault / "Web Articles" / "uncited.md").write_text(
                "Intro.\n\nThe widget ships in March 2026 to all tiers.\n",
                encoding="utf-8",
            )
            # synthesis cites only `cited.md`, which does NOT contain the fact
            (vault / "Web Articles" / "cited.md").write_text(
                "Different content entirely.\n", encoding="utf-8"
            )
            syn_fm = {"sources": ["vault/Web Articles/cited.md"]}
            attr = cz.attribute_fact(
                "The widget ships in March 2026 to all tiers.", vault, syn_fm
            )
            # verbatim hit exists but is uncited → intersection empty → falls back
            # to the matched set (still verbatim, but the point is it is NOT silently
            # attributed to a cited source it didn't come from).
            self.assertEqual(attr["provenance"], "verbatim")
            self.assertEqual(attr["sources"], ["vault/Web Articles/uncited.md"])

    def test_synthesized_is_inferred_with_underlying_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Web Articles").mkdir(parents=True)
            (vault / "Web Articles" / "a.md").write_text("alpha content\n", encoding="utf-8")
            syn_fm = {"sources": ["vault/Web Articles/a.md", "vault/Web Articles/b.md"]}
            attr = cz.attribute_fact(
                "A synthesized cross-source generalization with no verbatim home.",
                vault,
                syn_fm,
            )
            self.assertEqual(attr["provenance"], "synthesized")
            self.assertEqual(attr["tag"], "*[INFERRED]*")
            self.assertEqual(
                attr["sources"],
                ["vault/Web Articles/a.md", "vault/Web Articles/b.md"],
            )


class MakeClaimLineTests(unittest.TestCase):
    def test_extracted_line_has_no_tag(self):
        line = cz.make_claim_line("Widget ships March 2026", "", "claim-widget")
        self.assertEqual(line, "- Widget ships March 2026 {#claim-widget}")

    def test_inferred_line_carries_tag_before_anchor(self):
        line = cz.make_claim_line("Synthesized claim", "*[INFERRED]*", "claim-syn")
        self.assertEqual(line, "- Synthesized claim *[INFERRED]* {#claim-syn}")


def _wiki_with_vault(tmp: str):
    """A synthetic wiki (in-memory pages) + on-disk vault for trace, sharing one
    verbatim source so the merge path exercises the EXTRACTED branch."""
    vault = Path(tmp)
    (vault / "Web Articles").mkdir(parents=True)
    (vault / "Web Articles" / "src.md").write_text(
        "Intro.\n\nThe orchestrator caps concurrency at 16 workers.\n",
        encoding="utf-8",
    )
    pages = {
        "syn": _page(
            "pages/syntheses/syn.md",
            "syntheses",
            {"sources": ["vault/Web Articles/src.md", "vault/Web Articles/other.md"]},
            "# Syn\n\nA synthesis narrative.\n",
        ),
        "agent-harness": _page(
            "pages/concepts/agent-harness.md",
            "concepts",
            {"sources": ["vault/Web Articles/src.md"]},
            "# Agent harness\n\n## Key attributes\n\n- Runs subagents. {#claim-runs}\n",
        ),
    }
    return vault, pages


class BuildPlanTests(unittest.TestCase):
    def test_merge_row_full_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, pages = _wiki_with_vault(tmp)
            facts = [{"fact": "The orchestrator caps concurrency at 16 workers.",
                      "target": "agent-harness"}]
            plan = cz.build_plan(facts, pages, vault, "syn")
            self.assertEqual(plan["summary"]["merge"], 1)
            row = plan["rows"][0]
            self.assertEqual(row["status"], "merge")
            self.assertEqual(row["provenance"], "verbatim")
            self.assertEqual(row["tag"], "")
            self.assertEqual(row["sources"], ["vault/Web Articles/src.md"])
            # src already cited on the target → nothing new to add
            self.assertEqual(row["sources_to_add"], [])
            self.assertTrue(row["claim_line"].startswith("- The orchestrator caps"))
            self.assertIn("{#claim-", row["claim_line"])

    def test_dedup_marks_already_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, pages = _wiki_with_vault(tmp)
            facts = [{"fact": "Runs subagents.", "target": "agent-harness"}]
            plan = cz.build_plan(facts, pages, vault, "syn")
            self.assertEqual(plan["summary"]["already_present"], 1)
            self.assertEqual(plan["rows"][0]["status"], "already_present")

    def test_unhomed_when_no_or_unknown_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, pages = _wiki_with_vault(tmp)
            facts = [
                {"fact": "Homeless fact.", "target": None},
                {"fact": "Routed to a page that does not exist.", "target": "nope"},
            ]
            plan = cz.build_plan(facts, pages, vault, "syn")
            self.assertEqual(plan["summary"]["unhomed"], 2)
            self.assertTrue(all(r["status"] == "unhomed" for r in plan["rows"]))

    def test_claim_id_unique_vs_existing_and_within_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, pages = _wiki_with_vault(tmp)
            # Page already has {#claim-runs}; route two facts that slugify to the
            # same base, both to this page → all three IDs must be distinct.
            pages["agent-harness"]["body"] += "- Caps workers. {#claim-caps-workers}\n"
            facts = [
                {"fact": "Caps workers hard at peak load.", "target": "agent-harness"},
                {"fact": "Caps workers gently otherwise.", "target": "agent-harness"},
            ]
            plan = cz.build_plan(facts, pages, vault, "syn")
            ids = [r["claim_id"] for r in plan["rows"] if r["status"] == "merge"]
            self.assertEqual(len(ids), 2)
            self.assertEqual(len(set(ids)), 2)  # distinct from each other
            self.assertNotIn("claim-caps-workers", ids)  # distinct from existing anchor

    def test_synthesized_fact_adds_underlying_sources_to_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, pages = _wiki_with_vault(tmp)
            # A fact with no verbatim home → INFERRED, sources = synthesis underlying.
            # Target cites only src.md, so `other.md` is new to add.
            facts = [{"fact": "Cross-source insight with no single verbatim home anywhere.",
                      "target": "agent-harness"}]
            plan = cz.build_plan(facts, pages, vault, "syn")
            row = plan["rows"][0]
            self.assertEqual(row["status"], "merge")
            self.assertEqual(row["provenance"], "synthesized")
            self.assertIn("*[INFERRED]*", row["claim_line"])
            self.assertEqual(row["sources_to_add"], ["vault/Web Articles/other.md"])

    def test_missing_synthesis_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault, pages = _wiki_with_vault(tmp)
            with self.assertRaises(KeyError):
                cz.build_plan([], pages, vault, "no-such-syn")


if __name__ == "__main__":
    unittest.main(verbosity=2)
