#!/usr/bin/env python3
"""Append one row to wiki/.meta/corroborate_log.jsonl.

Called by `/wiki-corroborate` (one row per flag searched, not per skill
invocation) and back-filled by the wiki-ingestor (sets `flag_cleared_at_ingest`
on Y at the verify-clearing takeaway gate).

Schema documented in wiki/CLAUDE.md under "Telemetry".

Usage:
    # Initial write — one row per flag searched (user_action defaults to deferred).
    python append_corroborate_log.py --wiki <wiki-root> \\
        --page-slug claude-managed-agents --page-type entity \\
        --page-bucket B --callout-text "the 100KB persistent-memory cap..." \\
        --candidates-returned 5 \\
        --candidates-by-tier '{"tier_1":2,"tier_2":2,"tier_3":1}' \\
        --include-tier-3-flag false \\
        --user-action deferred

    # Backfill on capture — flips action and records the captured vault path.
    python append_corroborate_log.py --wiki <wiki-root> --backfill \\
        --page-slug claude-managed-agents \\
        --user-picked "https://docs.anthropic.com/..." \\
        --user-action captured \\
        --captured-to-vault "vault/Web Articles/2026-05-15-..."

    # Backfill at ingest — flag cleared, set the timestamp on the most recent
    # row matching --page-slug AND user_action=captured.
    python append_corroborate_log.py --wiki <wiki-root> --backfill-ingest \\
        --page-slug claude-managed-agents
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


VALID_USER_ACTIONS = {"captured", "skipped", "deferred"}
VALID_PAGE_BUCKETS = {"A", "B", "C", "D"}
VALID_PAGE_TYPES = {"concept", "entity", "comparison", "synthesis"}


def optional_int(raw: str) -> int | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return int(raw)


def optional_str(raw: str) -> str | None:
    if raw is None or raw == "" or raw.lower() == "null":
        return None
    return raw


def optional_bool(raw: str) -> bool:
    if raw is None or raw == "" or raw.lower() == "null":
        return False
    if raw.lower() in ("true", "1", "yes"):
        return True
    if raw.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"--include-tier-3-flag must be true/false: got {raw!r}")


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


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append/backfill rows in .meta/corroborate_log.jsonl")
    p.add_argument("--wiki", required=True, help="Path to the wiki root")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--backfill", action="store_true",
                      help="Mutate the most recent row matching --page-slug (and user_action=deferred unless --user-picked given) — sets user_picked / user_action / captured_to_vault.")
    mode.add_argument("--backfill-ingest", action="store_true",
                      help="Set flag_cleared_at_ingest=now on the most recent row matching --page-slug AND user_action=captured (ingestor's verify-clearing gate Y branch).")
    # Initial-write fields
    p.add_argument("--page-slug", required=True)
    p.add_argument("--page-type", choices=sorted(VALID_PAGE_TYPES), default=None)
    p.add_argument("--page-bucket", choices=sorted(VALID_PAGE_BUCKETS), default=None)
    p.add_argument("--callout-text", default=None)
    p.add_argument("--candidates-returned", type=optional_int, default=None)
    p.add_argument("--candidates-by-tier", type=candidates_dict, default={})
    p.add_argument("--include-tier-3-flag", type=optional_bool, default=False)
    # Action fields
    p.add_argument("--user-picked", type=optional_str, default=None,
                   help="URL the user chose at the candidate-review gate")
    p.add_argument("--user-action", choices=sorted(VALID_USER_ACTIONS), default="deferred")
    p.add_argument("--captured-to-vault", type=optional_str, default=None,
                   help="Vault path the picked URL was captured to via /wiki-capture")
    # Cost fields — all null in v1 (main-session only); reserved for future
    p.add_argument("--total-tokens", type=optional_int, default=None)
    p.add_argument("--tool-uses", type=optional_int, default=None)
    p.add_argument("--duration-ms", type=optional_int, default=None)
    return p.parse_args(argv)


def build_row(args: argparse.Namespace) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "page_slug": args.page_slug,
        "page_type": args.page_type,
        "page_bucket": args.page_bucket,
        "callout_text": args.callout_text,
        "candidates_returned": args.candidates_returned,
        "candidates_by_tier": args.candidates_by_tier,
        "include_tier_3_flag": args.include_tier_3_flag,
        "user_picked": args.user_picked,
        "user_action": args.user_action,
        "captured_to_vault": args.captured_to_vault,
        "flag_cleared_at_ingest": None,
        "total_tokens": args.total_tokens,
        "tool_uses": args.tool_uses,
        "duration_ms": args.duration_ms,
    }


def _read_rows(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        return []
    with log_path.open("r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _write_rows(log_path: Path, rows: list[dict]) -> None:
    with log_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _backfill_capture(rows: list[dict], args: argparse.Namespace) -> int:
    """Set user_picked / user_action / captured_to_vault on the most recent
    row matching page_slug AND user_action='deferred'. Returns row index."""
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("page_slug") == args.page_slug and r.get("user_action") == "deferred":
            if args.user_picked is not None:
                r["user_picked"] = args.user_picked
            r["user_action"] = args.user_action
            if args.captured_to_vault is not None:
                r["captured_to_vault"] = args.captured_to_vault
            return i
    raise SystemExit(f"error: no deferred row for page_slug={args.page_slug!r} to backfill")


def _backfill_ingest(rows: list[dict], args: argparse.Namespace) -> int:
    """Set flag_cleared_at_ingest=now on the most recent row matching
    page_slug AND user_action='captured'. Returns row index."""
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("page_slug") == args.page_slug and r.get("user_action") == "captured":
            r["flag_cleared_at_ingest"] = datetime.now().isoformat(timespec="seconds")
            return i
    raise SystemExit(f"error: no captured row for page_slug={args.page_slug!r} to flag_cleared")


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    wiki_root = Path(args.wiki).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    meta_dir = wiki_root / ".meta"
    meta_dir.mkdir(exist_ok=True)
    log_path = meta_dir / "corroborate_log.jsonl"

    if args.backfill or args.backfill_ingest:
        rows = _read_rows(log_path)
        if not rows:
            print(f"error: {log_path} is empty — nothing to backfill", file=sys.stderr)
            return 2
        if args.backfill:
            idx = _backfill_capture(rows, args)
            _write_rows(log_path, rows)
            print(f"backfilled row {idx} for page_slug={args.page_slug} (action={args.user_action})")
        else:
            idx = _backfill_ingest(rows, args)
            _write_rows(log_path, rows)
            print(f"backfilled row {idx} flag_cleared_at_ingest for page_slug={args.page_slug}")
        return 0

    # Initial write — require the schema-shape fields
    missing = [f for f in ("page_type", "page_bucket") if getattr(args, f.replace("-", "_")) is None]
    if missing:
        print(f"error: --{', --'.join(missing)} required on initial write", file=sys.stderr)
        return 2

    row = build_row(args)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"appended 1 row to {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
