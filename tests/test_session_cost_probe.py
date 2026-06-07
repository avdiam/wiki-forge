#!/usr/bin/env python3
"""Unit tests for session_cost_probe.py — transcript-window cost aggregation.

Stdlib unittest only. Byte-identical across the live (dev/tests/ ->
wiki/.scripts) and wiki-forge (tests/ -> skills/wiki-init/templates/scripts)
layouts via the upward path probe below — same shape as test_log_writer.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Locate the helper-scripts dir in either the live (dev/tests/ -> wiki/.scripts)
# or wiki-forge (tests/ -> skills/wiki-init/templates/scripts) layout.
here = Path(__file__).resolve()


def _scripts_dir() -> Path:
    for cand in (
        here.parents[2] / "wiki" / ".scripts",
        here.parents[1] / "skills" / "wiki-init" / "templates" / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))

import session_cost_probe as scp  # noqa: E402


def _asst(output, inp, cache_read, cache_create, ws=0, ts=None, tool_uses=0,
          tool_names=None):
    """Build one assistant-turn transcript entry.

    tool_uses adds that many generic ("X") tool_use blocks; tool_names adds one
    named tool_use block per entry (e.g. ["WebSearch"]) on top of those.
    """
    content = [{"type": "tool_use", "name": "X"} for _ in range(tool_uses)]
    for nm in tool_names or []:
        content.append({"type": "tool_use", "name": nm})
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "content": content,
                "usage": {
                    "output_tokens": output,
                    "input_tokens": inp,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_create,
                    "server_tool_use": {"web_search_requests": ws},
                },
            },
        }
    )


class TestAggregate(unittest.TestCase):
    def setUp(self):
        # Lines 0,1 are pre-window. Window starts at since_line=2.
        self.lines = [
            json.dumps({"type": "user"}),
            # pre-window assistant anchor: context = 10+100+0 = 110
            _asst(50, 10, 100, 0, ts="2026-06-07T10:00:00.000Z"),
            # --- window starts here (since_line=2) ---
            json.dumps({"type": "user"}),  # non-assistant -> skipped
            _asst(80, 20, 200, 30, ws=3, ts="2026-06-07T10:00:10.000Z", tool_uses=2),
            _asst(120, 40, 500, 10, ws=4, ts="2026-06-07T10:01:00.000Z", tool_uses=1),
        ]

    def test_window_sums(self):
        r = scp.aggregate(self.lines, since_line=2, until_line=None)
        self.assertEqual(r["turns"], 2)
        self.assertEqual(r["output_tokens_pass"], 200)  # 80 + 120
        self.assertEqual(r["input_tokens_pass"], 60)  # 20 + 40
        self.assertEqual(r["cache_creation_pass"], 40)  # 30 + 10
        self.assertEqual(r["web_search_requests"], 7)  # 3 + 4
        self.assertEqual(r["websearch_calls"], 7)
        self.assertEqual(r["tool_uses"], 3)  # 2 + 1
        # context_post = last turn: 40 + 500 + 10 = 550
        self.assertEqual(r["context_tokens_post_pass"], 550)
        # context_pre = pre-window assistant: 10 + 100 + 0 = 110
        self.assertEqual(r["context_tokens_pre_pass"], 110)
        self.assertEqual(r["context_growth"], 440)  # 550 - 110
        # total_tokens = output + input + cache_creation = 200 + 60 + 40
        self.assertEqual(r["total_tokens"], 300)
        # duration: 10:00:10 -> 10:01:00 = 50s
        self.assertEqual(r["duration_ms"], 50_000)

    def test_websearch_client_tool_counted(self):
        # Claude Code WebSearch is a client tool: server_tool_use stays 0 but a
        # tool_use block named "WebSearch" is present. websearch_calls must count
        # it (the bug the 2026-06-07 smoke test caught).
        lines = [
            _asst(
                100, 10, 50, 5, ws=0,
                ts="2026-06-07T12:00:00.000Z",
                tool_names=["WebSearch", "WebFetch"],
            ),
        ]
        r = scp.aggregate(lines, since_line=0, until_line=None)
        self.assertEqual(r["web_search_requests"], 0)  # server counter unused
        self.assertEqual(r["websearch_tool_uses"], 1)
        self.assertEqual(r["websearch_calls"], 1)  # from the client tool_use
        self.assertEqual(r["webfetch_tool_uses"], 1)
        self.assertEqual(r["webfetch_calls"], 1)
        self.assertEqual(r["tool_uses"], 2)

    def test_websearch_calls_sums_both_surfaces(self):
        # A turn with both a server counter and a client WebSearch block sums.
        lines = [
            _asst(10, 1, 1, 1, ws=2, ts="2026-06-07T12:00:00.000Z",
                  tool_names=["WebSearch"]),
        ]
        r = scp.aggregate(lines, since_line=0, until_line=None)
        self.assertEqual(r["websearch_calls"], 3)  # 2 server + 1 client

    def test_skips_non_assistant(self):
        r = scp.aggregate(self.lines, since_line=2, until_line=None)
        # The user line in the window must not inflate turns.
        self.assertEqual(r["turns"], 2)

    def test_empty_window(self):
        r = scp.aggregate(self.lines, since_line=len(self.lines), until_line=None)
        self.assertEqual(r["turns"], 0)
        self.assertEqual(r["output_tokens_pass"], 0)
        self.assertIsNone(r["context_tokens_post_pass"])
        self.assertIsNone(r["duration_ms"])
        # No post context -> growth is None even though pre exists.
        self.assertIsNone(r["context_growth"])

    def test_malformed_line_tolerated(self):
        lines = self.lines + ["{ this is not json", ""]
        r = scp.aggregate(lines, since_line=2, until_line=None)
        self.assertEqual(r["turns"], 2)  # garbage + blank ignored

    def test_no_pre_window_anchor(self):
        # since_line=0 -> no prior assistant -> pre is None, growth None.
        r = scp.aggregate(self.lines, since_line=0, until_line=None)
        self.assertIsNone(r["context_tokens_pre_pass"])
        self.assertIsNone(r["context_growth"])
        # All three assistant entries (indices 1, 3, 4) are now in-window.
        self.assertEqual(r["turns"], 3)


class TestHelpers(unittest.TestCase):
    def test_project_slug(self):
        self.assertEqual(
            scp.project_slug("C:\\Users\\a\\p"),
            os.path.abspath("C:\\Users\\a\\p").replace(":", "-").replace("\\", "-").replace("/", "-"),
        )
        # Every :, \, / becomes - (mirrors Claude Code's dir naming).
        slug = scp.project_slug("/home/x/y")
        self.assertNotIn("/", slug)

    def test_parse_ts_z_suffix(self):
        dt = scp.parse_ts("2026-06-07T10:00:00.000Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)

    def test_parse_ts_bad(self):
        self.assertIsNone(scp.parse_ts(None))
        self.assertIsNone(scp.parse_ts("not-a-date"))

    def test_resolve_explicit_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as fh:
            path = fh.name
        try:
            self.assertEqual(scp.resolve_session_file(path, os.getcwd()), path)
            self.assertIsNone(
                scp.resolve_session_file(path + ".missing", os.getcwd())
            )
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
