#!/usr/bin/env python3
"""Smoke tests for `load_authoritative_categories` — the parser that reads
`wiki/CATEGORIES.md` at runtime (introduced by the 2026-05-13 schema
decomposition; see `dev/archive/2026-05-13-wiki-agents.md-refactor.md` § Finding 1).

The parser is intentionally naive — it looks for the exact H2 header
`## Authoritative category list` and the `` - `<slug>` — <description> ``
bullet shape. These tests pin the contract so format-drift is caught
before lint silently fails in production.

Run:  python wiki/.scripts/test_load_categories.py

Exit codes: 0 = all tests pass; 1 = at least one test failed.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Make lint_wiki importable.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lint_wiki import load_authoritative_categories

# Live wiki root (parent of .scripts/).
WIKI_ROOT = SCRIPT_DIR.parent


class TestLoadCategoriesLive(unittest.TestCase):
    """Smoke test against the live wiki/CATEGORIES.md file.

    Catches the parser-fragility failure mode where a future edit
    silently empties the parse — e.g., H2 demoted to H3, or backtick
    bullets replaced with asterisks. The parser's documented behavior
    is sys.exit(2) on empty; this test trips before lint would.
    """

    def test_live_categories_parse_nonzero(self):
        cats = load_authoritative_categories(WIKI_ROOT)
        self.assertGreaterEqual(
            len(cats), 1,
            "wiki/CATEGORIES.md must yield ≥1 category — parser likely broken "
            "by a format change (H2 header demoted? backtick bullets removed?)."
        )

    def test_live_returns_frozenset(self):
        cats = load_authoritative_categories(WIKI_ROOT)
        self.assertIsInstance(cats, frozenset)


class TestLoadCategoriesEdgeCases(unittest.TestCase):
    """In-memory edge cases that exercise the parser's invariants."""

    def _write_temp_categories(self, content):
        """Write CATEGORIES.md into a fresh temp wiki-root; return Path."""
        tmp = tempfile.mkdtemp(prefix="test_categories_")
        wiki_root = Path(tmp)
        (wiki_root / "CATEGORIES.md").write_text(content, encoding="utf-8")
        return wiki_root

    def test_minimal_valid_input(self):
        wiki_root = self._write_temp_categories(
            "# Categories\n\n"
            "## Authoritative category list\n\n"
            "- `foo` — Description of foo\n"
            "- `bar` — Description of bar\n"
        )
        cats = load_authoritative_categories(wiki_root)
        self.assertEqual(cats, frozenset({"foo", "bar"}))

    def test_terminates_at_next_h2(self):
        wiki_root = self._write_temp_categories(
            "## Authoritative category list\n\n"
            "- `foo` — Description\n"
            "\n"
            "## Adding a new category\n\n"
            "- `not-a-slug` — Should not be parsed (different section)\n"
        )
        cats = load_authoritative_categories(wiki_root)
        self.assertEqual(cats, frozenset({"foo"}))

    def test_terminates_at_h1(self):
        wiki_root = self._write_temp_categories(
            "## Authoritative category list\n\n"
            "- `foo` — Description\n"
            "\n"
            "# Another top-level section\n\n"
            "- `not-a-slug` — Should not be parsed\n"
        )
        cats = load_authoritative_categories(wiki_root)
        self.assertEqual(cats, frozenset({"foo"}))

    def test_h3_header_not_recognized(self):
        """Parser anchors on H2. H3 should NOT trigger list collection."""
        wiki_root = self._write_temp_categories(
            "### Authoritative category list\n\n"
            "- `foo` — Description\n"
        )
        # No H2 → in_list stays False → cats stays empty → sys.exit(2).
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                load_authoritative_categories(wiki_root)
        self.assertEqual(cm.exception.code, 2)

    def test_indented_bullet_lines_accepted(self):
        """`line.lstrip().startswith('- `')` accepts indented bullets."""
        wiki_root = self._write_temp_categories(
            "## Authoritative category list\n\n"
            "  - `foo` — Description (indented)\n"
            "- `bar` — Description\n"
        )
        cats = load_authoritative_categories(wiki_root)
        self.assertEqual(cats, frozenset({"foo", "bar"}))

    def test_non_bullet_lines_ignored(self):
        """Preamble text + interleaved prose lines don't break the parser."""
        wiki_root = self._write_temp_categories(
            "## Authoritative category list\n\n"
            "Some preamble paragraph.\n"
            "\n"
            "- `foo` — Description\n"
            "Another non-bullet line.\n"
            "- `bar` — Description\n"
        )
        cats = load_authoritative_categories(wiki_root)
        self.assertEqual(cats, frozenset({"foo", "bar"}))

    def test_frontmatter_before_header_ignored(self):
        """YAML frontmatter at the top of CATEGORIES.md doesn't interfere."""
        wiki_root = self._write_temp_categories(
            "---\n"
            "schema_version: 2\n"
            "not_a_source: true\n"
            "---\n\n"
            "# Wiki — Categories\n\n"
            "## Authoritative category list\n\n"
            "- `foo` — Description\n"
        )
        cats = load_authoritative_categories(wiki_root)
        self.assertEqual(cats, frozenset({"foo"}))

    def test_missing_file_exits_2(self):
        tmp = tempfile.mkdtemp(prefix="test_categories_")
        wiki_root = Path(tmp)
        # CATEGORIES.md intentionally NOT created.
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                load_authoritative_categories(wiki_root)
        self.assertEqual(cm.exception.code, 2)

    def test_empty_list_exits_2(self):
        """Header present but zero bullets → sys.exit(2)."""
        wiki_root = self._write_temp_categories(
            "## Authoritative category list\n\n"
            "(intentionally empty section)\n\n"
            "## Next section\n"
        )
        with patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                load_authoritative_categories(wiki_root)
        self.assertEqual(cm.exception.code, 2)

    def test_bullet_without_backticks_skipped(self):
        """A plain `- foo` bullet (no backticks) doesn't match the pattern."""
        wiki_root = self._write_temp_categories(
            "## Authoritative category list\n\n"
            "- foo — no backticks (skipped)\n"
            "- `bar` — Description (matches)\n"
        )
        cats = load_authoritative_categories(wiki_root)
        self.assertEqual(cats, frozenset({"bar"}))


if __name__ == "__main__":
    unittest.main()
