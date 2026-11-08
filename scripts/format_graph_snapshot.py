#!/usr/bin/env python3
"""Format a graph_analyzer.py JSON snapshot as the welcome-report Graph line.

Reads the JSON output of `graph_analyzer.py <wiki> --json` (from a file path
or stdin via `-`) and prints one line in the wake-up-drill welcome-report
format:

    hubs <slug-1> (N), <slug-2> (N), <slug-3> (N) | <orphan/component status> | typed: <C>c/<S>s/<U>u

Used by the wake-up drill (vault/.wiki-identity-bootstrap.md) to keep the
formatting logic out of the inlined heredoc.

Usage:
    python format_graph_snapshot.py /tmp/wiki-graph.json
    python format_graph_snapshot.py -            # read JSON from stdin
"""

from __future__ import annotations

import argparse
import json
import sys


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def format_graph_line(d: dict) -> str:
    hubs = d.get("top_hubs_inbound", [])[:3]
    orphans = len(d.get("orphans", []))
    comps = d.get("component_count", 1)
    typed = d.get("typed_edges", {})
    c = typed.get("contradicts", 0)
    s = typed.get("supersedes", 0)
    u = typed.get("superseded_by", 0)

    hub_str = ", ".join(f"{h['page']} ({h['inbound']})" for h in hubs)
    if orphans == 0:
        plural = "s" if comps != 1 else ""
        status = f"clean (0 orphans, {comps} component{plural})"
    else:
        status = f"{orphans} orphans, {comps} components"
    typed_str = "typed: none" if (c + s + u) == 0 else f"typed: {c}c/{s}s/{u}u"
    return f"hubs {hub_str} | {status} | {typed_str}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Format a graph_analyzer.py JSON snapshot as the welcome-report Graph line."
    )
    parser.add_argument(
        "graph_json",
        help="Path to the graph JSON file, or '-' to read from stdin.",
    )
    args = parser.parse_args(argv)

    try:
        if args.graph_json == "-":
            d = json.load(sys.stdin)
        else:
            with open(args.graph_json, encoding="utf-8") as f:
                d = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: failed to read graph JSON: {e}", file=sys.stderr)
        return 2

    print(format_graph_line(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
