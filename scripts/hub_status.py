#!/usr/bin/env python3
"""Wake-up drill helper — report wiki-forge's agent-hub presence (read-only).

Fail-open REST probe of the agent-hub's `/api/state` endpoint. Returns one JSON
line describing whether the hub is reachable and what the hub currently thinks of
`wiki-forge`'s presence. Used by the wake-up drill (vault/.wiki-identity-bootstrap.md)
to render the welcome report's `Hub:` line and to decide whether to surface the
opt-in question — the question only fires when `reachable` is true.

Why this is a *probe*, not a *register*:
  - The hub exposes NO REST registration endpoint. Its REST surface is only
    /api/state, /api/peek, /api/reset, /api/restart, /api/agents/{id}/disconnect,
    /api/purge (see ../mcp-agent-hub-agy/mcp_hub/hub.py). Registration exists ONLY
    as the `register_agent` MCP tool. So this script can read presence but cannot
    assert it — re-registration is a model-invoked MCP call, handled in the drill.
  - This is also why the script never refreshes `last_seen`: the hub only refreshes
    that on MCP `tools/call` (the FastMCP ActivityTracker middleware), not on REST
    reads. Hitting /api/state here is a pure read; it does not keep us "online".

Fail-OPEN and SILENT-on-error, exactly like .claude/hooks/hub_peek.py: any failure
(hub down, timeout, bad JSON) yields `{"reachable": false, ...}` and exit 0. A wake-up
drill must never be blocked or slowed by hub state.

Agent id + hub url are config, not secrets: localhost:8000, agent `wiki-forge`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_AGENT_ID = "wiki-forge"
DEFAULT_HUB_URL = "http://127.0.0.1:8000"
TIMEOUT_S = 2.0

# The hub marks an agent "stale" once last_seen is older than this many seconds
# (STALE_THRESHOLD in ../mcp-agent-hub-agy/mcp_hub/hub.py). /api/state already
# derives online/stale for us; we surface last_seen_age_s as an informational
# cross-check since s119 found the hub's online flag less reliable than age.
STALE_THRESHOLD_S = 90


def probe(agent_id: str, hub_url: str) -> dict:
    """Return a presence dict for `agent_id`. Never raises — fail-open."""
    absent = {
        "reachable": False,
        "registered": False,
        "status": "unreachable",
        "last_seen_age_s": None,
    }
    try:
        url = f"{hub_url}/api/state"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=TIMEOUT_S) as response:
            if response.status != 200:
                return absent
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return absent

    agents = data.get("agents", []) if isinstance(data, dict) else []
    row = next((a for a in agents if a.get("id") == agent_id), None)
    if row is None:
        # Hub answered, but wiki-forge has no row (never registered, or DB wiped).
        return {
            "reachable": True,
            "registered": False,
            "status": "absent",
            "last_seen_age_s": None,
        }

    last_seen = row.get("last_seen")
    age = None
    if isinstance(last_seen, (int, float)):
        age = max(0, int(time.time() - last_seen))
    return {
        "reachable": True,
        "registered": True,
        # /api/state already derives online/stale/offline; fall back to age if absent.
        "status": row.get("status")
        or ("stale" if (age is not None and age > STALE_THRESHOLD_S) else "online"),
        "last_seen_age_s": age,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Probe the agent-hub for wiki-forge's presence (read-only, fail-open).")
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID, help="This agent's hub id.")
    parser.add_argument("--hub-url", default=DEFAULT_HUB_URL, help="Hub base URL.")
    args = parser.parse_args(argv)

    print(json.dumps(probe(args.agent_id, args.hub_url)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
