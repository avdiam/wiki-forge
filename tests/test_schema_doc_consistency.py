"""Unit tests for `lint_wiki.check_schema_doc_consistency` — schema-doc drift.

Pins the integrity guard that descends from the never-built `schema_sync` check.
The wiki's schema version is a prose banner in `AGENTS.md`; `CLAUDE.md` is an
`@AGENTS.md` @-include pointer; `SCHEMA_CHANGELOG.md` records one entry per bump,
newest first. Two invariants:

A. AGENTS.md banner version == newest SCHEMA_CHANGELOG.md entry version.
B. CLAUDE.md is the `@AGENTS.md` pointer.

A missing file is itself an issue (the trio is load-bearing). Soft-warning only.

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
import lint_wiki as lw  # noqa: E402


# Realistic banner — matches the live AGENTS.md shape (blockquote + em-dash tail).
def _banner(v: int) -> str:
    return (
        "# LLM Wiki Schema\n\n"
        f"> **Schema version:** {v} — see [`SCHEMA_CHANGELOG.md`](SCHEMA_CHANGELOG.md) "
        "for change history. Bump on breaking changes.\n\n"
        "Body of the schema.\n"
    )


# Realistic changelog — newest entry first.
def _changelog(*versions: int) -> str:
    head = "# Schema changelog\n\nOne-line entry per bump. Newest first.\n\n---\n\n"
    entries = "".join(f"- **v{v} (2026-0{v}-01):** Change {v}.\n\n" for v in versions)
    return head + entries


# Realistic CLAUDE.md — the one-line @-include pointer + a trailing comment.
_POINTER = "@AGENTS.md\n\n<!-- 1-line @-include pointer; schema content lives in AGENTS.md. -->\n"


def _wiki(agents: str | None, claude: str | None, changelog: str | None) -> Path:
    """Materialize a tmp wiki root with the three schema docs (None = omit)."""
    root = Path(tempfile.mkdtemp())
    if agents is not None:
        (root / "AGENTS.md").write_text(agents, encoding="utf-8")
    if claude is not None:
        (root / "CLAUDE.md").write_text(claude, encoding="utf-8")
    if changelog is not None:
        (root / "SCHEMA_CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return root


def _kinds(issues: list[dict]) -> list[str]:
    return [i["kind"] for i in issues]


class CheckSchemaDocConsistencyTests(unittest.TestCase):
    def test_consistent_trio_clean(self):
        root = _wiki(_banner(4), _POINTER, _changelog(4, 3, 2, 1))
        self.assertEqual(lw.check_schema_doc_consistency(root), [])

    def test_newest_entry_is_the_one_compared(self):
        # Banner matches the NEWEST (first) entry, not just *some* entry.
        root = _wiki(_banner(4), _POINTER, _changelog(4, 3, 2, 1))
        self.assertEqual(lw.check_schema_doc_consistency(root), [])

    def test_banner_matches_older_entry_but_not_newest_flags(self):
        # Banner v3 while the changelog's newest is v4 → mismatch (the real failure
        # mode: a bump landed a changelog entry but the banner wasn't advanced).
        root = _wiki(_banner(3), _POINTER, _changelog(4, 3, 2, 1))
        issues = lw.check_schema_doc_consistency(root)
        self.assertEqual(_kinds(issues), ["version_mismatch"])
        self.assertIn("v3", issues[0]["detail"])
        self.assertIn("v4", issues[0]["detail"])

    def test_banner_ahead_of_changelog_flags(self):
        # Inverse: banner bumped to v5, changelog forgotten at v4.
        root = _wiki(_banner(5), _POINTER, _changelog(4, 3, 2, 1))
        self.assertEqual(_kinds(lw.check_schema_doc_consistency(root)), ["version_mismatch"])

    def test_broken_pointer_flagged(self):
        root = _wiki(_banner(4), "# Wiki\n\nNot a pointer.\n", _changelog(4))
        self.assertEqual(_kinds(lw.check_schema_doc_consistency(root)), ["broken_pointer"])

    def test_pointer_with_trailing_content_ok(self):
        # The live CLAUDE.md has a comment line after the pointer — must still pass.
        root = _wiki(_banner(4), _POINTER, _changelog(4))
        self.assertEqual(lw.check_schema_doc_consistency(root), [])

    def test_missing_banner_flagged(self):
        root = _wiki("# LLM Wiki Schema\n\nNo version banner here.\n", _POINTER, _changelog(4))
        self.assertEqual(_kinds(lw.check_schema_doc_consistency(root)), ["missing_banner"])

    def test_missing_changelog_entry_flagged(self):
        root = _wiki(_banner(4), _POINTER, "# Schema changelog\n\nNo entries yet.\n")
        self.assertEqual(_kinds(lw.check_schema_doc_consistency(root)), ["missing_changelog_entry"])

    def test_missing_agents_file_flagged(self):
        root = _wiki(None, _POINTER, _changelog(4))
        self.assertEqual(_kinds(lw.check_schema_doc_consistency(root)), ["missing_file"])

    def test_missing_changelog_file_flagged(self):
        root = _wiki(_banner(4), _POINTER, None)
        self.assertEqual(_kinds(lw.check_schema_doc_consistency(root)), ["missing_file"])

    def test_missing_claude_file_flagged(self):
        root = _wiki(_banner(4), None, _changelog(4))
        self.assertEqual(_kinds(lw.check_schema_doc_consistency(root)), ["missing_file"])

    def test_multiple_issues_accumulate(self):
        # Banner/changelog mismatch AND a broken pointer at once.
        root = _wiki(_banner(3), "not a pointer\n", _changelog(4))
        kinds = _kinds(lw.check_schema_doc_consistency(root))
        self.assertIn("version_mismatch", kinds)
        self.assertIn("broken_pointer", kinds)

    def test_two_digit_versions_parse(self):
        # Guard the \d+ (not \d) — v10 must not read as v1.
        root = _wiki(_banner(10), _POINTER, _changelog(10, 9))
        self.assertEqual(lw.check_schema_doc_consistency(root), [])


if __name__ == "__main__":
    unittest.main()
