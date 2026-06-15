"""Unit tests for `lint_wiki.check_claim_ids` — claim-ID anchor hygiene (A2).

Pins the two issue kinds:
- DUPLICATE: the same `{#claim-<slug>}` more than once on one page (anchors must
  be unique per page so `[[page#claim-id]]` resolves to a single claim).
- MALFORMED: a `{#claim...}` anchor whose slug is not `[a-z0-9-]+`.
And the scope/opt-in contract:
- Only concept/entity/comparison/synthesis pages (claim-tag scope); source pages
  are exempt.
- Uniqueness is PER PAGE — the same slug on two different pages is fine.
- Absence of any anchor is never an issue (opt-in convention).

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import sys
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
import lint_wiki as lw


def _page(slug: str, body: str, ptype: str = "concepts") -> dict:
    """Minimal pages-dict entry — check_claim_ids reads only type + body + path."""
    return {slug: {"path": f"pages/{ptype}/{slug}.md", "type": ptype, "body": body}}


class CheckClaimIdsTests(unittest.TestCase):
    def test_unique_well_formed_ids_clean(self):
        pages = _page(
            "a",
            "A claim. {#claim-one}\n\nAnother claim. {#claim-two}\n",
        )
        self.assertEqual(lw.check_claim_ids(pages), [])

    def test_no_anchors_clean(self):
        self.assertEqual(lw.check_claim_ids(_page("a", "Plain prose, no anchors.")), [])

    def test_duplicate_flagged(self):
        pages = _page("a", "First. {#claim-dup}\n\nSecond. {#claim-dup}\n")
        issues = lw.check_claim_ids(pages)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["claim_id"], "claim-dup")
        self.assertIn("duplicate", issues[0]["reason"])

    def test_malformed_uppercase(self):
        issues = lw.check_claim_ids(_page("a", "Claim. {#claim-BadSlug}"))
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["reason"], "malformed slug")
        self.assertEqual(issues[0]["claim_id"], "{#claim-BadSlug}")

    def test_malformed_underscore(self):
        issues = lw.check_claim_ids(_page("a", "Claim. {#claim-bad_slug}"))
        self.assertEqual([i["reason"] for i in issues], ["malformed slug"])

    def test_malformed_space_after_hash(self):
        issues = lw.check_claim_ids(_page("a", "Claim. {# claim-foo}"))
        self.assertEqual([i["reason"] for i in issues], ["malformed slug"])

    def test_source_pages_exempt(self):
        pages = _page("s", "Dup. {#claim-x}\n\nDup. {#claim-x}\n", ptype="sources")
        self.assertEqual(lw.check_claim_ids(pages), [])

    def test_same_slug_across_pages_is_fine(self):
        pages = {}
        pages.update(_page("a", "Claim. {#claim-shared}"))
        pages.update(_page("b", "Claim. {#claim-shared}"))
        self.assertEqual(lw.check_claim_ids(pages), [])

    def test_well_formed_not_flagged_as_malformed(self):
        # A valid anchor matches the loose pattern too — must not be double-counted.
        issues = lw.check_claim_ids(_page("a", "Claim. {#claim-good-slug-2026}"))
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
