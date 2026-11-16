#!/usr/bin/env python3
"""Single-command wake-up drill aggregator: graph snapshot + drill state.

Replaces the two-step sequence in vault/.wiki-identity-bootstrap.md steps 5-6:

    # Old (three commands):
    python ${CLAUDE_PLUGIN_ROOT}/scripts/graph_analyzer.py wiki --json > /tmp/wiki-graph.json
    python ${CLAUDE_PLUGIN_ROOT}/scripts/format_graph_snapshot.py /tmp/wiki-graph.json
    python ${CLAUDE_PLUGIN_ROOT}/scripts/drill_state.py wiki --graph-json /tmp/wiki-graph.json

    # New (one command):
    python ${CLAUDE_PLUGIN_ROOT}/scripts/run_drill.py wiki

Builds the graph once and passes the result in-memory to both
format_graph_snapshot and drill_state, avoiding a second full graph rebuild.

Output JSON shape:
  {
    "graph_line": "<formatted Graph line ready for welcome report>",
    "lint_counter":     <int>,
    "lint_overdue":     <bool>,
    "ingest_count":     <int>,
    "overview": {
        "marker_ingest":     <int>,
        "stale_by":          <int>,
        "fresh":             <bool>,
        "should_rewrite":    <bool>,
        "trigger_reasons":   [<str>, ...]
    },
    "backlog": {
        "pending":           <int>,
        "state":             "all_clear" | "untriaged" | "triaged" | "stale",
        "triage_file":       <str | null>,
        "triage_created":    <str | null>,
        "batch_label":       <str | null>,
        "batch_first_slug":  <str | null>,
        "stale_count":       <int>
    },
    "scale_audit":  {...},   # Mode 7 re-audit detection (see drill_state.py)
    "verify_queue": {        # verify-flag ages; age is a lower bound from last_updated
        "total": <int>, "aged": <int>, "older_than_days": 60,
        "oldest_days": <int | null>, "oldest_slug": <str | null>,
        "aged_buckets": {"A": <int>, "B": <int>, "C": <int>}
    }
  }

Usage:
    python ${CLAUDE_PLUGIN_ROOT}/scripts/run_drill.py wiki
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from graph_analyzer import analyze as graph_analyze  # noqa: E402
from format_graph_snapshot import format_graph_line  # noqa: E402
from drill_state import build_state  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Wake-up drill aggregator: graph + lint counter + overview + backlog state."
    )
    parser.add_argument("wiki_root", help="Path to the wiki root (e.g., wiki).")
    args = parser.parse_args(argv)

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(
            f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)",
            file=sys.stderr,
        )
        return 2

    # Build graph once. Stash to a temp file so drill_state can pass the path
    # to check_overview_triggers without a second full graph rebuild.
    graph_data = graph_analyze(wiki_root)
    graph_line = format_graph_line(graph_data)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="wiki-graph-")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, default=list)
        state = build_state(wiki_root, tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    result = {"graph_line": graph_line, **state}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
