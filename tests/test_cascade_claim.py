"""Unit tests for `cascade_claim` — claim-level evidence-chain cascade (A3 v1).

Pins the load-bearing contract:
- ATTRIBUTION PRECEDENCE: inline quote-attribution > verbatim trace >
  single-source page > unattributable. A multi-source page's full `sources:`
  set is NEVER the fallback (the S5 false-positive trap).
- S5 GUARD: a page citing a *different* source is excluded from a source's
  blast radius even when it wikilinks the at-risk page. This is the whole point
  — the cascade follows the evidence chain, not the wikilink graph.
- AT-RISK GATE: only anchored claims on at-risk pages (verify / AMBIGUOUS /
  INFERRED / contradicted) enter the queue; clean anchored claims do not.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

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
import cascade_claim as cc


def _page(path, ptype, fm, body):
    return {"path": path, "type": ptype, "frontmatter": fm, "body": body}


# A synthetic wiki replicating the session-107 S5 specimen (kaitai-dong shape):
#   - `person`: entity, two sources (byline + linkedin), verify: true. Carries an
#     anchored AMBIGUOUS bio claim attributed inline to `linkedin`, plus another
#     unanchored AMBIGUOUS bio claim.
#   - `topic-a`, `topic-b`: concepts that cite ONLY `byline` and wikilink to
#     `person` (the "inbound citations"). They must NEVER appear in linkedin's
#     blast radius.
PERSON_BODY = """# Person

## Key attributes

- **Current role (LinkedIn):** Senior scientist at BigCorp Labs. *[AMBIGUOUS]* {#claim-role}
  > "I am a Senior scientist at BigCorp Labs." — [[linkedin]]
- **Education:** PhD, Somewhere University. *[AMBIGUOUS]*

## Relationships

- Cited at [[topic-a]] and [[topic-b]]
"""


def _wiki():
    return {
        "person": _page(
            "pages/entities/person.md",
            "entities",
            {
                "verify": True,
                "sources": [
                    "vault/Web Articles/byline.md",
                    "vault/Web Articles/linkedin.md",
                ],
            },
            PERSON_BODY,
        ),
        "topic-a": _page(
            "pages/concepts/topic-a.md",
            "concepts",
            {"sources": ["vault/Web Articles/byline.md"]},
            "# Topic A\n\nThe person's taxonomy framing. [[person]]\n",
        ),
        "topic-b": _page(
            "pages/concepts/topic-b.md",
            "concepts",
            {"sources": ["vault/Web Articles/byline.md"]},
            "# Topic B\n\nMore byline-sourced material. [[person]]\n",
        ),
    }


class SourceSlugTests(unittest.TestCase):
    def test_stems_from_full_paths(self):
        fm = {"sources": ["vault/Web Articles/2026-06-10-foo.md", "vault/bar.md"]}
        self.assertEqual(cc.source_slugs(fm), {"2026-06-10-foo", "bar"})

    def test_empty_and_non_list(self):
        self.assertEqual(cc.source_slugs({}), set())
        self.assertEqual(cc.source_slugs({"sources": "vault/x.md"}), set())
        self.assertEqual(cc.source_slugs({"sources": ["  ", ""]}), set())


class AnchoredClaimTests(unittest.TestCase):
    def test_finds_id_line_and_text(self):
        body = "# H\n\n- **Role:** Senior scientist. *[AMBIGUOUS]* {#claim-role}\n"
        claims = list(cc.iter_anchored_claims(body))
        self.assertEqual(len(claims), 1)
        cid, line, text = claims[0]
        self.assertEqual(cid, "claim-role")
        self.assertEqual(line, 3)
        # bullet + tag stripped; bold markers survive in the extracted sentence
        self.assertIn("Senior scientist", text)
        self.assertNotIn("AMBIGUOUS", text)

    def test_no_anchor_yields_nothing(self):
        self.assertEqual(list(cc.iter_anchored_claims("# H\n\nplain claim.\n")), [])


class InlineAttributionTests(unittest.TestCase):
    def test_found_on_next_line(self):
        lines = PERSON_BODY.splitlines()
        # claim-role is on line 5 (1-based) of PERSON_BODY
        role_line = next(
            i for i, l in enumerate(lines, 1) if "{#claim-role}" in l
        )
        self.assertEqual(cc.inline_attribution(lines, role_line), "linkedin")

    def test_stops_at_blank_line(self):
        lines = ["claim here {#claim-x}", "", "> quote — [[faraway]]"]
        self.assertIsNone(cc.inline_attribution(lines, 1))

    def test_none_when_no_attribution(self):
        lines = ["claim here {#claim-x}", "no attribution follows"]
        self.assertIsNone(cc.inline_attribution(lines, 1))

    def test_mid_line_wikilink_not_attribution(self):
        # a wikilink that isn't a trailing `— [[slug]]` must not be picked up
        lines = ["see [[other]] for context {#claim-x}"]
        self.assertIsNone(cc.inline_attribution(lines, 1))


class AttributePrecedenceTests(unittest.TestCase):
    def test_inline_wins_over_page_sources(self):
        pages = _wiki()
        role_line = next(
            i for i, l in enumerate(PERSON_BODY.splitlines(), 1) if "{#claim-role}" in l
        )
        attr = cc.attribute_claim(pages, None, "person", role_line, "Senior scientist at BigCorp Labs.")
        self.assertEqual(attr["provenance"], "inline-attribution")
        self.assertEqual(attr["sources"], {"linkedin"})

    def test_verbatim_when_no_inline(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Web Articles").mkdir(parents=True)
            (vault / "Web Articles" / "src.md").write_text(
                "Intro.\n\nThe widget ships in March 2026 to all tiers.\n",
                encoding="utf-8",
            )
            pages = {
                "p": _page(
                    "pages/concepts/p.md",
                    "concepts",
                    {"sources": ["vault/Web Articles/src.md", "vault/other.md"]},
                    "# P\n\nThe widget ships in March 2026 to all tiers. {#claim-ship}\n",
                )
            }
            attr = cc.attribute_claim(
                pages, vault, "p", 3, "The widget ships in March 2026 to all tiers."
            )
            self.assertEqual(attr["provenance"], "verbatim")
            self.assertEqual(attr["sources"], {"src"})

    def test_single_source_page_fallback(self):
        pages = {
            "p": _page(
                "pages/concepts/p.md",
                "concepts",
                {"sources": ["vault/only.md"]},
                "# P\n\nA synthesized verdict. *[INFERRED]* {#claim-v}\n",
            )
        }
        attr = cc.attribute_claim(pages, None, "p", 3, "A synthesized verdict.")
        self.assertEqual(attr["provenance"], "single-source-page")
        self.assertEqual(attr["sources"], {"only"})

    def test_unattributable_multi_source_no_inline(self):
        # the S5 trap: a synthesized claim on a multi-source page must NOT fall
        # back to the page's full source set.
        pages = {
            "p": _page(
                "pages/concepts/p.md",
                "concepts",
                {"sources": ["vault/a.md", "vault/b.md"]},
                "# P\n\nA cross-source synthesis. *[INFERRED]* {#claim-s}\n",
            )
        }
        attr = cc.attribute_claim(pages, None, "p", 3, "A cross-source synthesis.")
        self.assertEqual(attr["provenance"], "unattributable")
        self.assertEqual(attr["sources"], set())


class DependentsTests(unittest.TestCase):
    def test_s5_guard_excludes_other_source_pages(self):
        pages = _wiki()
        deps = cc.dependents_of_source(pages, None, "linkedin")
        slugs = {d["slug"] for d in deps}
        # only `person` cites linkedin; topic-a/topic-b cite only byline
        self.assertEqual(slugs, {"person"})

    def test_byline_dependents_are_the_inbound_pages(self):
        pages = _wiki()
        deps = cc.dependents_of_source(pages, None, "byline")
        slugs = {d["slug"] for d in deps}
        self.assertEqual(slugs, {"person", "topic-a", "topic-b"})

    def test_source_summary_page_excluded(self):
        # a source-type page that cites the source is the transit layer, not a
        # dependent claim — it must not appear in the blast radius.
        pages = _wiki()
        pages["linkedin-src"] = _page(
            "pages/sources/linkedin.md",
            "sources",
            {"sources": ["vault/Web Articles/linkedin.md"]},
            "# LinkedIn source summary\n\nProfile restated.\n",
        )
        deps = cc.dependents_of_source(pages, None, "linkedin")
        self.assertEqual({d["slug"] for d in deps}, {"person"})

    def test_anchored_claim_refinement_and_nudge(self):
        pages = _wiki()
        deps = cc.dependents_of_source(pages, None, "linkedin")
        person = next(d for d in deps if d["slug"] == "person")
        self.assertEqual([c["claim_id"] for c in person["anchored_claims"]], ["claim-role"])
        # two AMBIGUOUS markers on the page, one accounted for by the anchored
        # claim -> one unanchored AMBIGUOUS remaining (the nudge to anchor it).
        self.assertEqual(person["unanchored_ambiguous"], 1)


class AtRiskTests(unittest.TestCase):
    def test_verify_true(self):
        info = _page("p", "entities", {"verify": True}, "x")
        self.assertIn("page verify: true", cc.at_risk_reasons(info, "a claim {#claim-x}"))

    def test_ambiguous_claim(self):
        info = _page("p", "entities", {}, "x")
        self.assertIn("claim *[AMBIGUOUS]*", cc.at_risk_reasons(info, "claim *[AMBIGUOUS]* {#c}"))

    def test_contradiction_callout(self):
        body = "## Contradictions\n> [!WARNING] Contradiction Detected: X vs Y.\n"
        info = _page("p", "concepts", {}, body)
        self.assertIn("page contradicted", cc.at_risk_reasons(info, "claim {#c}"))

    def test_clean_claim_not_at_risk(self):
        info = _page("p", "concepts", {"sources": ["vault/a.md"]}, "x")
        self.assertEqual(cc.at_risk_reasons(info, "an extracted claim {#c}"), [])


class CascadeEndToEndTests(unittest.TestCase):
    def test_kaitai_shape_blast_radius(self):
        pages = _wiki()
        rows = cc.cascade(pages, None)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["claim_id"], "claim-role")
        self.assertEqual(row["provenance"], "inline-attribution")
        self.assertEqual(row["sources"], ["linkedin"])
        # the entire blast radius is `person` itself — none of the inbound
        # byline-citing pages leak in.
        self.assertEqual(cc._dependent_count(row), 1)
        dep_slugs = {
            d["slug"] for b in row["blast_radius"] for d in b["dependents"]
        }
        self.assertEqual(dep_slugs, {"person"})
        self.assertNotIn("topic-a", dep_slugs)
        self.assertNotIn("topic-b", dep_slugs)

    def test_clean_anchored_claim_not_queued(self):
        pages = {
            "p": _page(
                "pages/concepts/p.md",
                "concepts",
                {"sources": ["vault/a.md", "vault/b.md"]},
                "# P\n\nAn extracted, untagged claim. {#claim-clean}\n",
            )
        }
        self.assertEqual(cc.cascade(pages, None), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
