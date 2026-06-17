"""Unit tests for `check_manifest` — plugin-manifest ↔ on-disk inventory drift.

Pins the regression guard filed session 114 after the manifest was found
advertising "twelve skills" against 17 on disk. Two clauses in
`.claude-plugin/plugin.json`'s `description` — "Ships <word> skills (…)" and
"<word> subagents (…)" — are each asserted against the real `skills/` and
`agents/` trees, in either direction, plus a count-word ↔ list-length check.

Most cases run against tmp fixtures; the final case runs the check against the
LIVE wiki-forge tree and asserts it's clean — that's the actual guard the next
skill ship trips if it forgets to update the manifest. It skips if the manifest
can't be located (defensive; it always can in a real checkout).

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import json
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
import check_manifest as cm  # noqa: E402


# Real on-disk inventory (bare names, wiki- prefix stripped) — the fixtures below
# mirror this shape so a clean fixture genuinely exercises the equality path.
_SKILLS = [
    "ask", "capture", "ingest", "crystallize", "query", "graph", "summarize", "trace",
    "corroborate", "resolve-contradictions", "lint", "scale-audit", "log",
    "overview", "triage", "retract", "stub", "init",
]
_SUBAGENTS = ["ingestor", "librarian", "linter", "curator"]


def _description(skills_word: str, skills, agents_word: str, agents) -> str:
    return (
        f"Turn your agent into a knowledge compiler. Ships {skills_word} skills "
        f"({', '.join(skills)}) and {agents_word} subagents ({', '.join(agents)}), "
        "plus hybrid search and other things. Based on the LLM Wiki pattern."
    )


def _forge(description: str, skills, agents) -> Path:
    """Materialize a tmp wiki-forge root: manifest + wiki-prefixed skill/agent trees."""
    root = Path(tempfile.mkdtemp())
    plugin_dir = root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "wiki-forge", "description": description, "version": "0.0.0"}),
        encoding="utf-8",
    )
    skills_dir = root / "skills"
    skills_dir.mkdir()
    for s in skills:
        (skills_dir / f"wiki-{s}").mkdir()  # on disk the dirs carry the wiki- prefix
    agents_dir = root / "agents"
    agents_dir.mkdir()
    for a in agents:
        (agents_dir / f"wiki-{a}.md").write_text("agent\n", encoding="utf-8")
    return root


def _kinds(issues) -> list:
    return [i["kind"] for i in issues]


class WordToIntTests(unittest.TestCase):
    def test_ones_and_teens(self):
        self.assertEqual(cm.word_to_int("four"), 4)
        self.assertEqual(cm.word_to_int("seventeen"), 17)

    def test_tens(self):
        self.assertEqual(cm.word_to_int("twenty"), 20)
        self.assertEqual(cm.word_to_int("forty"), 40)

    def test_compound(self):
        self.assertEqual(cm.word_to_int("twenty-one"), 21)
        self.assertEqual(cm.word_to_int("thirty-five"), 35)

    def test_case_insensitive(self):
        self.assertEqual(cm.word_to_int("Seventeen"), 17)

    def test_unrecognized(self):
        self.assertIsNone(cm.word_to_int("zillion"))
        self.assertIsNone(cm.word_to_int("17"))


class CheckManifestTests(unittest.TestCase):
    def test_consistent_manifest_clean(self):
        root = _forge(_description("eighteen", _SKILLS, "four", _SUBAGENTS), _SKILLS, _SUBAGENTS)
        self.assertEqual(cm.check_manifest(root), [])

    def test_wiki_prefix_stripped_on_disk(self):
        # Disk dirs are `wiki-capture`; manifest lists bare `capture`. Must match.
        root = _forge(_description("two", ["capture", "ingest"], "one", ["linter"]),
                      ["capture", "ingest"], ["linter"])
        self.assertEqual(cm.check_manifest(root), [])

    def test_skills_count_word_disagrees_with_list(self):
        # Word says sixteen, list has 18 → count mismatch (and counts still match disk set).
        root = _forge(_description("sixteen", _SKILLS, "four", _SUBAGENTS), _SKILLS, _SUBAGENTS)
        self.assertEqual(_kinds(cm.check_manifest(root)), ["skills_count_mismatch"])

    def test_skill_on_disk_not_advertised(self):
        # The real session-114 failure: a skill shipped to disk un-listed.
        advertised = _SKILLS[:-1]  # drop "init" from the manifest (17 advertised), keep it on disk (18)
        root = _forge(_description("seventeen", advertised, "four", _SUBAGENTS), _SKILLS, _SUBAGENTS)
        issues = cm.check_manifest(root)
        self.assertEqual(_kinds(issues), ["skills_disk_mismatch"])
        self.assertIn("init", issues[0]["detail"])
        self.assertIn("not advertised", issues[0]["detail"])

    def test_skill_advertised_not_on_disk(self):
        # Inverse: manifest lists a skill that doesn't exist on disk.
        advertised = _SKILLS + ["ghost"]
        root = _forge(_description("nineteen", advertised, "four", _SUBAGENTS), _SKILLS, _SUBAGENTS)
        issues = cm.check_manifest(root)
        self.assertEqual(_kinds(issues), ["skills_disk_mismatch"])
        self.assertIn("ghost", issues[0]["detail"])
        self.assertIn("not on disk", issues[0]["detail"])

    def test_subagent_disk_mismatch(self):
        root = _forge(_description("eighteen", _SKILLS, "three", _SUBAGENTS[:3]), _SKILLS, _SUBAGENTS)
        self.assertEqual(_kinds(cm.check_manifest(root)), ["subagents_disk_mismatch"])

    def test_subagent_count_mismatch(self):
        root = _forge(_description("eighteen", _SKILLS, "five", _SUBAGENTS), _SKILLS, _SUBAGENTS)
        self.assertEqual(_kinds(cm.check_manifest(root)), ["subagents_count_mismatch"])

    def test_both_clauses_accumulate(self):
        root = _forge(_description("sixteen", _SKILLS, "five", _SUBAGENTS), _SKILLS, _SUBAGENTS)
        kinds = _kinds(cm.check_manifest(root))
        self.assertIn("skills_count_mismatch", kinds)
        self.assertIn("subagents_count_mismatch", kinds)

    def test_bad_count_word(self):
        root = _forge(_description("many", _SKILLS, "four", _SUBAGENTS), _SKILLS, _SUBAGENTS)
        self.assertEqual(_kinds(cm.check_manifest(root)), ["bad_count_word_skills"])

    def test_unparseable_skills_clause(self):
        desc = "Ships a bunch of capabilities and four subagents (ingestor, librarian, linter, curator)."
        root = _forge(desc, _SKILLS, _SUBAGENTS)
        self.assertIn("unparseable_skills", _kinds(cm.check_manifest(root)))

    def test_missing_manifest(self):
        root = Path(tempfile.mkdtemp())  # no .claude-plugin/plugin.json
        (root / "skills").mkdir()
        self.assertEqual(_kinds(cm.check_manifest(root)), ["missing_manifest"])


class LiveManifestTests(unittest.TestCase):
    """The actual regression guard — run the check against the shipped tree."""

    def setUp(self):
        self.forge_root = SCRIPTS.parent  # <wiki-forge>/scripts -> <wiki-forge>
        if not (self.forge_root / ".claude-plugin" / "plugin.json").is_file():
            self.skipTest("live plugin.json not found (unexpected in a real checkout)")

    def test_live_manifest_in_sync(self):
        issues = cm.check_manifest(self.forge_root)
        self.assertEqual(issues, [], f"manifest drift: {issues}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
