"""Unit tests for the `--backfill-cost` mode of the corroborate + contradiction
telemetry appenders, focused on the 2026-06-08 batch-unit fields:
`context_tokens_pre_pass`, `context_growth`, and the auto-stamped
`flags_in_batch`.

Pins the batch-unit convention: context discipline is gated on per-invocation
`context_growth` (persisted now, was dropped in v2), and throughput is
normalized per-flag via `flags_in_batch` (= the number of rows sharing the
batch_id, stamped automatically at backfill regardless of which probe fields
are present). Both appenders share this machinery verbatim, so each assertion
runs against both.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import json
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

import append_corroborate_log as corr
import append_contradiction_log as contra


def _make_wiki(tmp: Path) -> Path:
    (tmp / ".meta").mkdir(parents=True, exist_ok=True)
    # resolve_wiki_root requires a CLAUDE.md marker at the root.
    (tmp / "CLAUDE.md").write_text("# test wiki\n", encoding="utf-8")
    return tmp


def _read(wiki: Path, name: str) -> list:
    path = wiki / ".meta" / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestCorroborateBackfillCost(unittest.TestCase):
    """append_corroborate_log.py --backfill-cost stamps the new fields."""

    def _seed_batch(self, wiki: Path, batch_id: str, slugs):
        for slug in slugs:
            rc = corr.main([
                "--wiki", str(wiki),
                "--page-slug", slug, "--page-type", "entity", "--page-bucket", "B",
                "--candidates-returned", "2",
                "--batch-id", batch_id, "--skill-version", "corroborate-v2",
            ])
            self.assertEqual(rc, 0)

    def test_growth_and_flags_in_batch_stamped_on_all_rows(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = _make_wiki(Path(d))
            self._seed_batch(wiki, "abc123-40", ["alpha", "beta", "gamma"])
            rc = corr.main([
                "--wiki", str(wiki), "--backfill-cost", "--batch-id", "abc123-40",
                "--total-tokens", "120000", "--tool-uses", "15",
                "--duration-ms", "144000", "--websearch-calls", "10",
                "--context-tokens-post", "90742", "--context-tokens-pre", "78900",
                "--context-growth", "11842", "--output-tokens-pass", "31857",
            ])
            self.assertEqual(rc, 0)
            rows = _read(wiki, "corroborate_log.jsonl")
            self.assertEqual(len(rows), 3)
            for r in rows:
                self.assertEqual(r["context_tokens_pre_pass"], 78900)
                self.assertEqual(r["context_growth"], 11842)
                self.assertEqual(r["context_tokens_post_pass"], 90742)
                # flags_in_batch is the batch size, auto-stamped on every row.
                self.assertEqual(r["flags_in_batch"], 3)

    def test_initial_write_has_null_new_fields(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = _make_wiki(Path(d))
            self._seed_batch(wiki, "z-1", ["solo"])
            row = _read(wiki, "corroborate_log.jsonl")[0]
            self.assertIsNone(row["context_growth"])
            self.assertIsNone(row["context_tokens_pre_pass"])
            self.assertIsNone(row["flags_in_batch"])

    def test_null_growth_does_not_overwrite_other_fields(self):
        # A probe that found no pre-anchor passes context_growth=null; the
        # null-skip must not block flags_in_batch or the present fields.
        with tempfile.TemporaryDirectory() as d:
            wiki = _make_wiki(Path(d))
            self._seed_batch(wiki, "n-0", ["one", "two"])
            rc = corr.main([
                "--wiki", str(wiki), "--backfill-cost", "--batch-id", "n-0",
                "--context-tokens-post", "50000",
                # --context-growth omitted -> None -> skipped
            ])
            self.assertEqual(rc, 0)
            rows = _read(wiki, "corroborate_log.jsonl")
            for r in rows:
                self.assertEqual(r["context_tokens_post_pass"], 50000)
                self.assertIsNone(r["context_growth"])
                self.assertEqual(r["flags_in_batch"], 2)  # still stamped


class TestContradictionBackfillCost(unittest.TestCase):
    """append_contradiction_log.py shares the machinery verbatim."""

    def _seed_pair(self, wiki: Path, batch_id: str, pair_key: str):
        a, b = pair_key.split("|")
        rc = contra.main([
            "--wiki", str(wiki),
            "--pair-key", pair_key,
            "--pair-a-slug", a, "--pair-a-type", "concept",
            "--pair-b-slug", b, "--pair-b-type", "source",
            "--shape", "concept-source",
            "--batch-id", batch_id, "--skill-version", "resolve-v2",
        ])
        self.assertEqual(rc, 0)

    def test_growth_and_flags_in_batch_stamped(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = _make_wiki(Path(d))
            self._seed_pair(wiki, "p-9", "agents-md|2026-02-12-src")
            self._seed_pair(wiki, "p-9", "rag|2026-03-01-src")
            rc = contra.main([
                "--wiki", str(wiki), "--backfill-cost", "--batch-id", "p-9",
                "--context-tokens-post", "70000", "--context-tokens-pre", "60000",
                "--context-growth", "10000",
            ])
            self.assertEqual(rc, 0)
            rows = _read(wiki, "contradiction_log.jsonl")
            self.assertEqual(len(rows), 2)
            for r in rows:
                self.assertEqual(r["context_growth"], 10000)
                self.assertEqual(r["context_tokens_pre_pass"], 60000)
                self.assertEqual(r["flags_in_batch"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
