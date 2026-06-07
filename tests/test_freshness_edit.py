"""Unit tests for `check_source_freshness.edit_source_page_for_drift`.

Forward-insurance test. The function writes the `source_drifted: true`
frontmatter flag and the warning callout onto a source page when drift
is detected. The flag must land INSIDE the existing YAML block (before
the closing fence) so YAML parsers — including `lint_wiki.py`'s
`check_drifted_sources` — can find it. The bug fixed in session 65
(2026-05-14) was placing the field in a second `---...---` block after
the original closing fence, making it unreachable. These tests pin the
correct shape so the regression can't recur silently.

Six cases:
- fresh_page: no source_drifted field → field injected inside YAML,
  callout in body, both parseable.
- idempotent_remark: page with source_drifted: true → no change, return
  False.
- defensive_guard_false: page with source_drifted: false (defensive
  case, no current writer produces this) → no change, return False
  (avoids duplicate-key YAML).
- callout_present_field_missing: body already has callout but field
  was absent → field injected, callout NOT duplicated.
- empty_body: page with only frontmatter, no body → still produces
  well-formed output.
- malformed_no_opening_fence: text doesn't start with --- → return False.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

# Locate the helper-scripts dir in either the live (dev/tests/ -> wiki/.scripts)
# or the wiki-forge (tests/ -> skills/wiki-init/templates/scripts) layout, so
# this file stays byte-identical across both repos.
def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (
        here.parents[2] / "wiki" / ".scripts",
        here.parents[1] / "skills" / "wiki-init" / "templates" / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))
from check_source_freshness import edit_source_page_for_drift
from frontmatter import parse_frontmatter


def _write_temp(content: str) -> Path:
    """Write content to a temp .md file and return its Path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def _yaml_block_count(text: str) -> int:
    """Count standalone `---` fence lines (frontmatter delimiters).

    A well-formed page has exactly 2 (opening + closing). The session-65
    bug produced 4 (opening + closing + spurious-opening + spurious-closing).
    """
    return sum(1 for line in text.splitlines() if line.strip() == "---")


class EditSourcePageForDriftTests(unittest.TestCase):
    def setUp(self):
        self._temp_files: list[Path] = []

    def tearDown(self):
        for p in self._temp_files:
            try:
                p.unlink()
            except OSError:
                pass

    def _make(self, content: str) -> Path:
        p = _write_temp(content)
        self._temp_files.append(p)
        return p

    def test_fresh_page(self):
        """No source_drifted field → injected inside YAML; callout in body."""
        original = (
            "---\n"
            "title: Test Source\n"
            "type: source\n"
            "category: test\n"
            "stale: false\n"
            "---\n"
            "\n"
            "# Test Source\n"
            "\n"
            "Body content here.\n"
        )
        path = self._make(original)
        result = edit_source_page_for_drift(path)
        self.assertTrue(result, "should return True on successful mark")

        after = path.read_text(encoding="utf-8")
        # Exactly 2 frontmatter fences — not 4 (the session-65 bug shape).
        self.assertEqual(
            _yaml_block_count(after), 2,
            f"expected 2 --- fences, got {_yaml_block_count(after)}:\n{after}"
        )
        # YAML parser sees the field as True.
        fm = parse_frontmatter(after)
        self.assertIs(fm.get("source_drifted"), True)
        # Callout is in the body, not eaten into the frontmatter.
        self.assertIn("> [!WARNING] Source drift detected", after)
        # H1 title preserved.
        self.assertIn("# Test Source", after)

    def test_idempotent_remark(self):
        """Page already marked source_drifted: true → no change."""
        already_marked = (
            "---\n"
            "title: Test Source\n"
            "type: source\n"
            "source_drifted: true\n"
            "---\n"
            "\n"
            "> [!WARNING] Source drift detected\n"
            "> ...\n"
            "\n"
            "# Test Source\n"
        )
        path = self._make(already_marked)
        before = path.read_text(encoding="utf-8")
        result = edit_source_page_for_drift(path)
        after = path.read_text(encoding="utf-8")
        self.assertFalse(result, "should return False on idempotent re-mark")
        self.assertEqual(before, after, "file should be unchanged")

    def test_defensive_guard_pre_existing_false(self):
        """Page with source_drifted: false → skipped (no duplicate-key YAML)."""
        with_false = (
            "---\n"
            "title: Test Source\n"
            "source_drifted: false\n"
            "---\n"
            "\n"
            "# Test\n"
        )
        path = self._make(with_false)
        before = path.read_text(encoding="utf-8")
        result = edit_source_page_for_drift(path)
        after = path.read_text(encoding="utf-8")
        self.assertFalse(result, "should return False when field exists as non-True")
        self.assertEqual(before, after, "file should be unchanged")
        # No duplicate source_drifted line.
        self.assertEqual(
            len(re.findall(r"^source_drifted:", after, re.MULTILINE)), 1,
            "should not produce duplicate source_drifted key"
        )

    def test_callout_present_field_missing(self):
        """Body already has the callout but field was stripped → inject field only."""
        callout_only = (
            "---\n"
            "title: Test Source\n"
            "stale: false\n"
            "---\n"
            "\n"
            "> [!WARNING] Source drift detected\n"
            "> Existing callout text.\n"
            "\n"
            "# Test\n"
        )
        path = self._make(callout_only)
        result = edit_source_page_for_drift(path)
        after = path.read_text(encoding="utf-8")
        self.assertTrue(result)
        fm = parse_frontmatter(after)
        self.assertIs(fm.get("source_drifted"), True)
        # Callout NOT duplicated.
        callout_count = after.count("> [!WARNING] Source drift detected")
        self.assertEqual(callout_count, 1,
                         f"callout should not be duplicated; got {callout_count}")

    def test_empty_body(self):
        """Page with only frontmatter and nothing else still produces valid output."""
        frontmatter_only = (
            "---\n"
            "title: Test\n"
            "type: source\n"
            "---\n"
        )
        path = self._make(frontmatter_only)
        result = edit_source_page_for_drift(path)
        self.assertTrue(result)
        after = path.read_text(encoding="utf-8")
        self.assertEqual(_yaml_block_count(after), 2)
        fm = parse_frontmatter(after)
        self.assertIs(fm.get("source_drifted"), True)

    def test_malformed_no_opening_fence(self):
        """File without opening --- → returns False, no write."""
        malformed = "# Just a markdown file\n\nNo frontmatter.\n"
        path = self._make(malformed)
        before = path.read_text(encoding="utf-8")
        result = edit_source_page_for_drift(path)
        after = path.read_text(encoding="utf-8")
        self.assertFalse(result)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
