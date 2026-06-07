"""Unit tests for `_log_writer` — the shared shell behind the append_*_log.py
telemetry writers.

Regression pin for the 2026-06-07 consolidation that extracted the duplicated
outer shell (coercion helpers + JSONL I/O + URL normalization) out of the
eight `append_*` scripts into one module. The tech-debt entry that motivated
the consolidation named the failure mode it guards against: "a divergence bug
where two scripts handle `<usage>`/URL parsing differently and produce
inconsistent rows." With one implementation that can't happen — these tests
pin that one implementation so the helpers can't silently drift.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import argparse
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

import _log_writer as lw


class TestCoercion(unittest.TestCase):
    """The argparse `type=` coercion helpers: empty/'null' → absent."""

    def test_optional_int(self):
        for raw in ("", "null", "NULL"):
            self.assertIsNone(lw.optional_int(raw))
        self.assertEqual(lw.optional_int("5"), 5)
        self.assertEqual(lw.optional_int("-3"), -3)
        self.assertIsNone(lw.optional_int(None))

    def test_optional_float(self):
        self.assertIsNone(lw.optional_float(""))
        self.assertIsNone(lw.optional_float("null"))
        self.assertEqual(lw.optional_float("1.5"), 1.5)
        self.assertEqual(lw.optional_float("0"), 0.0)

    def test_optional_str(self):
        for raw in ("", "null", "Null"):
            self.assertIsNone(lw.optional_str(raw))
        self.assertEqual(lw.optional_str("hello"), "hello")
        # A non-empty string that merely contains 'null' is preserved.
        self.assertEqual(lw.optional_str("nullable"), "nullable")

    def test_strict_bool_true_false(self):
        for raw in ("true", "1", "yes", "TRUE", "Yes"):
            self.assertIs(lw.strict_bool(raw), True)
        for raw in ("false", "0", "no", "FALSE", "No"):
            self.assertIs(lw.strict_bool(raw), False)

    def test_strict_bool_rejects_garbage(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            lw.strict_bool("maybe")
        # Unlike optional_bool, empty string is NOT accepted.
        with self.assertRaises(argparse.ArgumentTypeError):
            lw.strict_bool("")

    def test_optional_bool_empty_is_false(self):
        for raw in ("", "null", None):
            self.assertIs(lw.optional_bool(raw), False)
        self.assertIs(lw.optional_bool("true"), True)
        self.assertIs(lw.optional_bool("no"), False)

    def test_optional_bool_rejects_garbage(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            lw.optional_bool("maybe")

    def test_split_slugs(self):
        self.assertEqual(lw.split_slugs(""), [])
        self.assertEqual(lw.split_slugs("a"), ["a"])
        # Whitespace stripped, empties dropped (trailing comma, double comma).
        self.assertEqual(lw.split_slugs("a, b ,,c,"), ["a", "b", "c"])


class TestUrlHelpers(unittest.TestCase):
    """domain_from_url + candidate_urls + candidates_dict (contradiction +
    corroborate logs share these exactly)."""

    def test_domain_from_url_host_plus_first_segment(self):
        self.assertEqual(lw.domain_from_url("https://arxiv.org/abs/2301.001"), "arxiv.org/abs")
        self.assertEqual(lw.domain_from_url("https://github.com/anthropic/x"), "github.com/anthropic")

    def test_domain_from_url_strips_www_and_bare_host(self):
        self.assertEqual(lw.domain_from_url("https://www.example.com"), "example.com")
        self.assertEqual(lw.domain_from_url("https://example.com/"), "example.com")

    def test_domain_from_url_no_scheme(self):
        # urlsplit dumps a scheme-less URL into .path — the fallback recovers host.
        self.assertEqual(lw.domain_from_url("medium.com/@author/post"), "medium.com/@author")

    def test_candidates_dict(self):
        self.assertEqual(lw.candidates_dict(""), {})
        self.assertEqual(lw.candidates_dict('{"tier_1": 2, "tier_2": 1}'), {"tier_1": 2, "tier_2": 1})

    def test_candidates_dict_rejects_non_int(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            lw.candidates_dict('{"tier_1": "two"}')
        with self.assertRaises(argparse.ArgumentTypeError):
            lw.candidates_dict("[1, 2]")

    def test_candidate_urls_none_vs_empty(self):
        # None (arg omitted) and "" (genuinely zero candidates) are distinct.
        self.assertIsNone(lw.candidate_urls(None))
        self.assertEqual(lw.candidate_urls(""), [])

    def test_candidate_urls_extracts_domains(self):
        raw = '[{"url": "https://arxiv.org/abs/1", "tier": "tier_1"}]'
        self.assertEqual(lw.candidate_urls(raw), [{"domain": "arxiv.org/abs", "tier": "tier_1"}])

    def test_candidate_urls_requires_url_and_tier(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            lw.candidate_urls('[{"url": "https://x.com/y"}]')


class TestJsonlIo(unittest.TestCase):
    """append_jsonl / read_rows / write_rows round-trip + resolve_wiki_root."""

    def test_append_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / ".meta" / "x.jsonl"
            lw.append_jsonl(log, {"a": 1})
            lw.append_jsonl(log, {"a": 2, "u": None})
            self.assertTrue(log.is_file())  # parent dir auto-created
            self.assertEqual(lw.read_rows(log), [{"a": 1}, {"a": 2, "u": None}])

    def test_append_jsonl_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / ".meta" / "x.jsonl"
            lw.append_jsonl(log, {"t": "café — déjà"})
            self.assertEqual(lw.read_rows(log)[0]["t"], "café — déjà")
            # ensure_ascii=False means the raw bytes carry the literal chars.
            self.assertIn("café", log.read_text(encoding="utf-8"))

    def test_read_rows_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(lw.read_rows(Path(d) / "nope.jsonl"), [])

    def test_write_rows_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "x.jsonl"
            lw.append_jsonl(log, {"a": 1})
            lw.write_rows(log, [{"b": 9}])
            self.assertEqual(lw.read_rows(log), [{"b": 9}])

    def test_resolve_wiki_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "wiki"
            root.mkdir()
            # No CLAUDE.md yet → None.
            self.assertIsNone(lw.resolve_wiki_root(str(root)))
            (root / "CLAUDE.md").write_text("# wiki\n", encoding="utf-8")
            self.assertEqual(lw.resolve_wiki_root(str(root)), root.resolve())


if __name__ == "__main__":
    unittest.main()
