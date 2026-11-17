"""Unit tests for `append_query_log` — the /wiki-ask router additions.

Pins the session-117 telemetry extension: the `ask` query_skill value, the
`routed_to` / `routed_from` / `routing_clarifications` fields, and the router-row
validation (ask requires routed_to; ask forces search_mode null). Earlier query
rows (query / graph / summarize) carry the three routing fields as null for a
uniform schema — pinned here too so a future build_row edit can't drop them.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import json
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

import append_query_log as aql  # noqa: E402


def _wiki() -> Path:
    """A tmp wiki root with the .meta dir the appender writes into."""
    root = Path(tempfile.mkdtemp())
    (root / ".meta").mkdir()
    (root / "CLAUDE.md").write_text("schema\n", encoding="utf-8")
    return root


def _rows(wiki: Path) -> list:
    log = wiki / ".meta" / "query_log.jsonl"
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


class RouterRowTests(unittest.TestCase):
    def test_ask_requires_routed_to(self):
        wiki = _wiki()
        rc = aql.main(["--wiki", str(wiki), "--query-skill", "ask",
                       "--query", "whats near rag", "--duration-ms", "100"])
        self.assertEqual(rc, 2)
        self.assertFalse((wiki / ".meta" / "query_log.jsonl").exists())

    def test_ask_router_row_shape(self):
        wiki = _wiki()
        rc = aql.main(["--wiki", str(wiki), "--query-skill", "ask",
                       "--query", "whats near rag", "--routed-to", "graph",
                       "--routing-clarifications", "1", "--duration-ms", "1200"])
        self.assertEqual(rc, 0)
        row = _rows(wiki)[0]
        self.assertEqual(row["query_skill"], "ask")
        self.assertEqual(row["routed_to"], "graph")
        self.assertEqual(row["routing_clarifications"], 1)
        self.assertIsNone(row["routed_from"])
        self.assertIsNone(row["search_mode"])  # router does no retrieval
        self.assertIsNone(row["total_tokens"])  # router does no model work

    def test_ask_forces_search_mode_null(self):
        # Even if a search-mode is passed, an ask row records null.
        wiki = _wiki()
        rc = aql.main(["--wiki", str(wiki), "--query-skill", "ask",
                       "--query", "x", "--routed-to", "query",
                       "--search-mode", "hybrid", "--duration-ms", "50"])
        self.assertEqual(rc, 0)
        self.assertIsNone(_rows(wiki)[0]["search_mode"])

    def test_routed_to_rejects_self_and_garbage(self):
        # argparse choices: 'ask' is not a valid route target, nor is junk.
        for bad in ("ask", "nonsense"):
            with self.assertRaises(SystemExit):
                aql.parse_args(["--wiki", "w", "--query-skill", "ask",
                                "--query", "x", "--routed-to", bad, "--duration-ms", "1"])


class DispatchedRowTests(unittest.TestCase):
    def test_graph_routed_from_ask(self):
        wiki = _wiki()
        rc = aql.main(["--wiki", str(wiki), "--query-skill", "graph",
                       "--query", "neighbors rag --depth 1",
                       "--pages-consulted", "rag,agentic-rag",
                       "--routed-from", "ask", "--duration-ms", "800"])
        self.assertEqual(rc, 0)
        row = _rows(wiki)[0]
        self.assertEqual(row["query_skill"], "graph")
        self.assertEqual(row["routed_from"], "ask")
        self.assertIsNone(row["routed_to"])
        self.assertIsNone(row["routing_clarifications"])

    def test_routed_from_only_accepts_ask(self):
        with self.assertRaises(SystemExit):
            aql.parse_args(["--wiki", "w", "--query-skill", "graph",
                            "--query", "x", "--routed-from", "query", "--duration-ms", "1"])


class DirectRowTests(unittest.TestCase):
    def test_direct_query_row_has_null_routing_fields(self):
        # An un-routed /wiki-query row still carries the three fields, all null.
        wiki = _wiki()
        rc = aql.main(["--wiki", str(wiki), "--query", "what is rag",
                       "--search-mode", "hybrid", "--duration-ms", "5000"])
        self.assertEqual(rc, 0)
        row = _rows(wiki)[0]
        self.assertIn("routed_to", row)
        self.assertIn("routed_from", row)
        self.assertIn("routing_clarifications", row)
        self.assertIsNone(row["routed_to"])
        self.assertIsNone(row["routed_from"])
        self.assertIsNone(row["routing_clarifications"])
        self.assertIsNone(row["pages_skimmed"])
        self.assertIsNone(row["pages_promoted"])

    def test_survey_read_split_fields(self):
        # s144: summarize rows record which pages were skimmed vs promoted; "" → [].
        wiki = _wiki()
        rc = aql.main(["--wiki", str(wiki), "--query-skill", "summarize", "--query", "topic",
                       "--search-mode", "hybrid", "--pages-consulted", "a,b,c",
                       "--pages-skimmed", "b,c", "--pages-promoted", "", "--duration-ms", "9"])
        self.assertEqual(rc, 0)
        row = _rows(wiki)[0]
        self.assertEqual(row["pages_skimmed"], ["b", "c"])
        self.assertEqual(row["pages_promoted"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
