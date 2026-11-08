#!/usr/bin/env python3
"""Shared outer shell for the append_*_log.py telemetry writers.

The eight `append_*` scripts each own a distinct JSONL (or markdown) row
schema, but the *outer shell* around that schema is identical across them:
argparse value coercion (`optional_int` / `optional_str` / `strict_bool` /
...), wiki-root validation, the JSONL append routine, and — for the two
URL-bearing logs (contradiction + corroborate) — full-file rewrite helpers
plus URL→domain normalization. This module holds that shared shell so the
per-log scripts stay thin: each keeps its own argparse contract, `build_row`,
and any single-caller helpers, and imports the rest from here.

Importing this module reconfigures stdout/stderr to UTF-8 (the same line every
appender used to carry) so non-ASCII paths print cleanly on cp1252/cp1253
consoles. Behavior of the coercion helpers and the append routine is verbatim
from the pre-consolidation scripts; pinned by dev/tests/test_log_writer.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ---------- argparse value coercion ----------
# All accept the CLI convention that an empty string or the literal "null"
# means "field absent" → None (or, for the bool variants, the documented
# default). Used as argparse `type=` callables.


def optional_int(raw: str) -> int | None:
    """Accept empty string or 'null' as None; otherwise coerce to int."""
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return int(raw)


def optional_float(raw: str) -> float | None:
    """Accept empty string or 'null' as None; otherwise coerce to float."""
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return float(raw)


def optional_str(raw: str) -> str | None:
    """Accept empty string or 'null' as None; otherwise pass through."""
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return raw


def strict_bool(raw: str) -> bool:
    """true/1/yes → True, false/0/no → False; anything else is an error.

    No None case — use this for required boolean flags.
    """
    low = raw.lower()
    if low in ("true", "1", "yes"):
        return True
    if low in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got '{raw}'")


def optional_bool(raw: str) -> bool:
    """Like strict_bool but treats empty string / 'null' as False rather than
    erroring — for optional boolean flags that default False."""
    if raw is None or raw == "" or raw.lower() == "null":
        return False
    if raw.lower() in ("true", "1", "yes"):
        return True
    if raw.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {raw!r}")


def split_slugs(raw: str) -> list[str]:
    """Comma-separated slug string → stripped, empty-filtered list."""
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


# ---------- candidate-URL helpers (contradiction + corroborate logs) ----------


def candidates_dict(raw: str) -> dict:
    """Parse --candidates-by-tier as a JSON object with int values per tier key."""
    if raw is None or raw == "":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"--candidates-by-tier must be JSON: {e}")
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--candidates-by-tier must be a JSON object")
    for k, v in parsed.items():
        if not isinstance(v, int):
            raise argparse.ArgumentTypeError(f"--candidates-by-tier values must be int (got {k}={v!r})")
    return parsed


def domain_from_url(url: str) -> str:
    """Extract host + first path segment from a URL, lowercased; strip 'www.'
    for normalization. The first segment is preserved (when present) because
    tier classification uses path-prefix patterns for vendor-org cases like
    github.com/anthropic and linkedin.com/in/<user>. urlsplit handles missing
    schemes by parsing the whole string as path — fall back accordingly so
    unparsable inputs still log SOMETHING auditable rather than empty."""
    parts = urlsplit(url.strip())
    if parts.netloc:
        host = parts.netloc.lower().strip()
        path = parts.path or ""
    else:
        # No scheme: urlsplit dumps everything into .path
        raw = parts.path or ""
        segs = raw.lstrip("/").split("/", 1)
        host = segs[0].lower().strip()
        path = "/" + segs[1] if len(segs) > 1 else ""
    if host.startswith("www."):
        host = host[4:]
    # Append first non-empty path segment when present
    first = path.lstrip("/").split("/", 1)[0]
    if first:
        return f"{host}/{first}"
    return host


def candidate_urls(raw: str) -> list[dict] | None:
    """Parse --candidate-urls as a JSON array of {url, tier} records. Each
    record's url is reduced to its host (via domain_from_url) before the row
    is written; tier is preserved verbatim. Returns a list of {domain, tier}
    records ready for the row, or None when the arg was omitted (distinguishes
    pre-fix rows from rows that genuinely saw zero candidates — those pass
    --candidate-urls '[]')."""
    if raw is None:
        return None
    if raw == "":
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"--candidate-urls must be JSON: {e}")
    if not isinstance(parsed, list):
        raise argparse.ArgumentTypeError("--candidate-urls must be a JSON array")
    out = []
    for item in parsed:
        if not isinstance(item, dict) or "url" not in item or "tier" not in item:
            raise argparse.ArgumentTypeError(f"each --candidate-urls record needs url+tier: {item!r}")
        out.append({"domain": domain_from_url(item["url"]), "tier": item["tier"]})
    return out


# ---------- wiki-root validation + JSONL I/O ----------


def resolve_wiki_root(raw: str) -> Path | None:
    """Resolve --wiki and confirm it looks like a wiki root (has CLAUDE.md).

    Returns the resolved Path, or prints the canonical error to stderr and
    returns None — callers do `if root is None: return 2`.
    """
    root = Path(raw).resolve()
    if not (root / "CLAUDE.md").is_file():
        print(f"error: {root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return None
    return root


def append_jsonl(log_path: Path, row: dict) -> None:
    """Append one JSON object as a line to log_path, creating its parent dir."""
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(log_path: Path) -> list[dict]:
    """Read a JSONL file into a list of dicts ([] if the file is absent)."""
    if not log_path.is_file():
        return []
    with log_path.open("r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def write_rows(log_path: Path, rows: list[dict]) -> None:
    """Rewrite a JSONL file from a list of dicts (for in-place backfills)."""
    with log_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------- --usage-json (shared by the Agent-dispatch appenders) ----------
# `parse_agent_usage.py` writes one JSON with every cost field. Appenders that
# log a subagent dispatch (ingest, lint, query, overview) accept
# `--usage-json <path>` and expand it into their ordinary cost flags before
# argparse runs, so each parser's contract (incl. required --duration-ms)
# stays unchanged. Explicit flags on the command line win over the file.

USAGE_FLAG_FIELDS = (
    ("--total-tokens", "total_tokens"),
    ("--input-tokens", "input_tokens"),
    ("--cache-creation-input-tokens", "cache_creation_input_tokens"),
    ("--cache-read-input-tokens", "cache_read_input_tokens"),
    ("--output-tokens", "output_tokens"),
    ("--tool-uses", "tool_uses"),
    ("--duration-ms", "duration_ms"),
)


def add_usage_json_arg(p: argparse.ArgumentParser) -> None:
    """Document --usage-json in --help (expanded earlier by inject_usage_argv)."""
    p.add_argument("--usage-json", metavar="PATH",
                   help="JSON from parse_agent_usage.py; fills any cost flag not given explicitly")


def inject_usage_argv(argv: list[str]) -> list[str]:
    """Replace `--usage-json PATH` (or `--usage-json=PATH`) with cost flags.

    A flag already present in argv is left alone. Null values in the JSON are
    skipped, so the appender's own default (None) applies — except
    --duration-ms, which the caller must then pass explicitly.
    """
    out: list[str] = []
    path = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--usage-json" and i + 1 < len(argv):
            path = argv[i + 1]
            i += 2
            continue
        if a.startswith("--usage-json="):
            path = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    if not path:
        return out
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    present = {a.split("=", 1)[0] for a in out if a.startswith("--")}
    for flag, field in USAGE_FLAG_FIELDS:
        if flag in present:
            continue
        val = data.get(field)
        if val is None:
            continue
        out += [flag, str(val)]
    return out
