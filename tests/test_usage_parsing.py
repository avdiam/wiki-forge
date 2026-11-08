"""Tests for `parse_agent_usage.py` and the appenders' `--usage-json` hook.

The skills that dispatch subagents (ingest, lint, query, summarize, overview)
no longer carry regex prose for the Agent tool's `<usage>` block; they pipe
the block into `parse_agent_usage.py` and hand its JSON to the appender with
`--usage-json`. This pins both halves:

- both block syntaxes (legacy `key: N`, current `<tag>N</tag>` with
  `subagent_tokens` -> `total_tokens`), multi-block summing, and the fixed
  `input_tokens`-inside-`cache_*_input_tokens` substring quirk;
- transcript enrichment: per-API-call dedup by message id, per-message model;
- `_log_writer.inject_usage_argv`: explicit flags win, nulls are skipped.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

here = Path(__file__).resolve()


def _scripts_dir() -> Path:
    for cand in (
        here.parents[2] / "wiki-forge" / "scripts",
        here.parents[1] / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


sys.path.insert(0, str(_scripts_dir()))

import parse_agent_usage as pau  # noqa: E402
from _log_writer import inject_usage_argv  # noqa: E402


class TestParseUsageText(unittest.TestCase):

    def test_current_xml_shape(self):
        text = ("<usage><subagent_tokens>84332</subagent_tokens>"
                "<tool_uses>7</tool_uses><duration_ms>40048</duration_ms></usage>")
        r = pau.parse_usage_text(text)
        self.assertEqual(r["total_tokens"], 84332)
        self.assertEqual(r["tool_uses"], 7)
        self.assertEqual(r["duration_ms"], 40048)
        self.assertIsNone(r["input_tokens"])
        self.assertEqual(r["blocks"], 1)

    def test_legacy_colon_shape(self):
        text = "report...\n<usage>total_tokens: 107145, tool_uses: 49, duration_ms: 428134</usage>\n"
        r = pau.parse_usage_text(text)
        self.assertEqual((r["total_tokens"], r["tool_uses"], r["duration_ms"]), (107145, 49, 428134))
        self.assertIsNone(r["output_tokens"])

    def test_full_legacy_shape(self):
        text = ("<usage>total_tokens: 158204, input_tokens: 122, "
                "cache_creation_input_tokens: 18432, cache_read_input_tokens: 138210, "
                "output_tokens: 1440, tool_uses: 47, duration_ms: 312405</usage>")
        r = pau.parse_usage_text(text)
        self.assertEqual(r["input_tokens"], 122)
        self.assertEqual(r["cache_creation_input_tokens"], 18432)
        self.assertEqual(r["cache_read_input_tokens"], 138210)
        self.assertEqual(r["output_tokens"], 1440)

    def test_substring_quirk_fixed(self):
        """`input_tokens` must not match inside `cache_read_input_tokens`."""
        for text in (
            "<usage>cache_read_input_tokens: 41610, total_tokens: 48210</usage>",
            "<usage><cache_read_input_tokens>41610</cache_read_input_tokens></usage>",
        ):
            r = pau.parse_usage_text(text)
            self.assertEqual(r["cache_read_input_tokens"], 41610)
            self.assertIsNone(r["input_tokens"])

    def test_multiple_blocks_sum(self):
        """Resumed agent (lint fix turn) or Mode B: every field sums, duration too."""
        text = ("<usage><subagent_tokens>46973</subagent_tokens><tool_uses>23</tool_uses>"
                "<duration_ms>272589</duration_ms></usage>\n...\n"
                "<usage><subagent_tokens>55642</subagent_tokens><tool_uses>7</tool_uses>"
                "<duration_ms>47535</duration_ms></usage>")
        r = pau.parse_usage_text(text)
        self.assertEqual(r["total_tokens"], 102615)
        self.assertEqual(r["tool_uses"], 30)
        self.assertEqual(r["duration_ms"], 320124)
        self.assertEqual(r["blocks"], 2)

    def test_explicit_total_beats_alias(self):
        r = pau.parse_usage_text("<usage>total_tokens: 10, subagent_tokens: 99</usage>")
        self.assertEqual(r["total_tokens"], 10)

    def test_no_block(self):
        r = pau.parse_usage_text("no usage here")
        for f in pau.COST_FIELDS:
            self.assertIsNone(r[f])
        self.assertEqual(r["blocks"], 0)

    def test_whitespace_tolerance(self):
        r = pau.parse_usage_text("<usage>total_tokens:107145, tool_uses:    49, duration_ms:\t428134</usage>")
        self.assertEqual((r["total_tokens"], r["tool_uses"], r["duration_ms"]), (107145, 49, 428134))


class TestTranscriptEnrichment(unittest.TestCase):

    def _write_transcript(self, root: str, cwd: str, agent_id: str) -> None:
        d = Path(root) / pau.project_slug(cwd) / "session-1" / "subagents"
        d.mkdir(parents=True)
        recs = [
            {"type": "user", "message": {"content": "go"}},
            # One API call split into two records sharing id + usage.
            {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5",
             "usage": {"input_tokens": 3, "cache_creation_input_tokens": 100,
                       "cache_read_input_tokens": 0, "output_tokens": 10},
             "content": [{"type": "text", "text": "hi"}]}},
            {"type": "assistant", "message": {"id": "m1", "model": "claude-sonnet-5-5",
             "usage": {"input_tokens": 3, "cache_creation_input_tokens": 100,
                       "cache_read_input_tokens": 0, "output_tokens": 10},
             "content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
            {"type": "assistant", "message": {"id": "m2", "model": "claude-sonnet-5-5",
             "usage": {"input_tokens": 1, "cache_creation_input_tokens": 5,
                       "cache_read_input_tokens": 100, "output_tokens": 20},
             "content": [{"type": "text", "text": "done"}]}},
        ]
        with open(d / f"agent-{agent_id}.jsonl", "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

    def test_dedup_and_model(self):
        with tempfile.TemporaryDirectory() as root:
            cwd = os.path.join(root, "proj")
            self._write_transcript(root, cwd, "abc")
            r = pau.parse_usage_text("<usage><subagent_tokens>500</subagent_tokens></usage>")
            pau.enrich(r, ["abc"], cwd, projects_root=root)
            self.assertEqual(r["total_tokens"], 500)          # harness figure kept
            self.assertEqual(r["input_tokens"], 4)            # 3 + 1, m1 counted once
            self.assertEqual(r["cache_creation_input_tokens"], 105)
            self.assertEqual(r["cache_read_input_tokens"], 100)
            self.assertEqual(r["output_tokens"], 30)
            self.assertEqual(r["assistant_turns"], 2)
            self.assertEqual(r["tool_uses"], 1)               # filled: block had none
            self.assertEqual(r["models"], ["claude-sonnet-5-5"])

    def test_missing_transcript(self):
        with tempfile.TemporaryDirectory() as root:
            r = pau.parse_usage_text("")
            pau.enrich(r, ["nope"], os.path.join(root, "proj"), projects_root=root)
            self.assertEqual(r["missing_transcripts"], ["nope"])
            self.assertIsNone(r["input_tokens"])


class TestInjectUsageArgv(unittest.TestCase):

    def test_expands_and_respects_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "u.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"total_tokens": 5, "input_tokens": None, "tool_uses": 2,
                           "duration_ms": 99}, fh)
            out = inject_usage_argv(["--wiki", "w", "--usage-json", p, "--tool-uses", "3"])
            self.assertNotIn("--usage-json", out)
            self.assertNotIn("--input-tokens", out)           # null skipped
            self.assertEqual(out[out.index("--total-tokens") + 1], "5")
            self.assertEqual(out[out.index("--duration-ms") + 1], "99")
            self.assertEqual(out.count("--tool-uses"), 1)     # explicit wins
            self.assertEqual(out[out.index("--tool-uses") + 1], "3")

    def test_equals_form_and_passthrough(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "u.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"duration_ms": 7}, fh)
            self.assertEqual(inject_usage_argv([f"--usage-json={p}"]), ["--duration-ms", "7"])
            self.assertEqual(inject_usage_argv(["--wiki", "w"]), ["--wiki", "w"])


if __name__ == "__main__":
    unittest.main()
