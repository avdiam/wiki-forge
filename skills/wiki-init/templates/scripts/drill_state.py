#!/usr/bin/env python3
"""Aggregate wake-up-drill state into a single JSON object.

Replaces drill steps 5 (lint counter), 7 (overview staleness + structural
trigger check), and 8 (ingest backlog state) in vault/.wiki-identity.md
with one script call. The welcome-report renderer reads this JSON and
fills in the report fields.

Output JSON shape (keys are stable; downstream parsers depend on them):
  {
    "lint_counter":     <int>,    # ingests since last lint entry in log.md
    "lint_overdue":     <bool>,   # lint_counter >= 10
    "ingest_count":     <int>,    # total ingest entries in log.md
    "overview": {
        "marker_ingest":     <int>,         # `Mechanical refresh at ingest #N` value, 0 if absent
        "stale_by":          <int>,         # ingest_count - marker_ingest
        "fresh":             <bool>,        # stale_by < 10
        "should_rewrite":    <bool>,        # check_overview_triggers verdict
        "trigger_reasons":   [<str>, ...]   # empty list when should_rewrite is false
    },
    "backlog": {
        "pending":         <int>,
        "state":           "all_clear" | "untriaged" | "triaged" | "stale",
        "triage_file":     <str | null>,    # project-relative path, e.g. dev/ingest-triage-2026-05-05.md
        "triage_created":  <str | null>,    # ISO date from frontmatter, when state in {triaged, stale}
        "batch_label":     <str | null>,    # e.g. "Batch 10", when state == triaged
        "batch_first_slug": <str | null>,   # first not-yet-ingested article in the next batch's body
        "stale_count":     <int>            # files added since triage; nonzero only when state == stale
    },
    "scale_audit": {                        # Mode 7 re-audit detection (scale_audit_probes.evaluate)
        "should_audit":    <bool>,          # any re-audit trigger fired against the live baseline
        "fired_count":     <int>,           # number of fired triggers (out of 5)
        "reasons":         [<str>, ...],    # short reason strings for fired triggers (banner text)
        "last_audit_date": <str | null>,    # ISO date of the last audit (from scale-audit-config.json)
        "configured":      <bool>           # whether a .meta/scale-audit-config.json baseline exists
    }
  }

Mailbox state is not aggregated here — drill step 1 owns the read-and-flip
sweep, which has side effects beyond JSON emission.

Field-only mode for the statusline (which only needs the lint counter):
    python drill_state.py wiki --field lint_counter

This emits one bare integer with no JSON wrapping. The statusline-side
formatting (e.g. `lint:N/10` vs `lint:N/10 OVERDUE`) stays in the shell
script — only the count comes from here.

Usage:
    python drill_state.py wiki                                      # JSON to stdout (default)
    python drill_state.py wiki --graph-json /tmp/wiki-graph.json   # reuse a stashed graph snapshot
    python drill_state.py wiki --field lint_counter                # statusline mode
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Local sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_overview_triggers import evaluate as evaluate_overview_triggers  # noqa: E402
from scale_audit_probes import evaluate as evaluate_scale_audit  # noqa: E402
from source_backlog import collect_source_hashes, compute_backlog  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


LINT_OVERDUE_THRESHOLD = 10
OVERVIEW_FRESH_THRESHOLD = 10  # < 10 ⇒ "fresh"; ≥ 10 surfaces the soft-refresh offer

OVERVIEW_MARKER_RE = re.compile(
    r"(?:Mechanical refresh|Last full rewrite) at ingest #(\d+)",
    re.IGNORECASE,
)
INGEST_LINE_RE = re.compile(r"^## \[.*?\] ingest \|", re.MULTILINE)
LINT_LINE_RE = re.compile(r"^## \[.*?\] lint ", re.MULTILINE)

TRIAGED_COUNT_RE = re.compile(r"(\d+) unprocessed vault files triaged", re.IGNORECASE)
NEXT_BATCH_RE = re.compile(
    r"^- \[ \] \*\*(Batch (\d+))\*\* — \d+ ingests \(([^)]+)\)",
    re.MULTILINE,
)
TRIAGE_STATUS_RE = re.compile(r"^status:\s*(\S+)", re.MULTILINE)
TRIAGE_CREATED_RE = re.compile(r"^created:\s*(\S+)", re.MULTILINE)


def compute_lint_state(log_text: str) -> tuple[int, int]:
    """Return (lint_counter, ingest_count). Counter resets on each `lint` line."""
    counter = 0
    total_ingests = 0
    for line in log_text.splitlines():
        if LINT_LINE_RE.match(line):
            counter = 0
        elif INGEST_LINE_RE.match(line):
            counter += 1
            total_ingests += 1
    return counter, total_ingests


def compute_overview_state(
    wiki_root: Path,
    graph_json_arg: str | None,
    ingest_count: int,
) -> dict:
    overview_path = wiki_root / "overview.md"
    marker_ingest = 0
    if overview_path.is_file():
        try:
            text = overview_path.read_text(encoding="utf-8", errors="ignore")
            matches = OVERVIEW_MARKER_RE.findall(text)
            if matches:
                marker_ingest = max(int(n) for n in matches)
        except OSError:
            pass

    stale_by = max(0, ingest_count - marker_ingest)

    triggers = evaluate_overview_triggers(wiki_root, graph_json_arg=graph_json_arg)
    return {
        "marker_ingest": marker_ingest,
        "stale_by": stale_by,
        "fresh": stale_by < OVERVIEW_FRESH_THRESHOLD,
        "should_rewrite": bool(triggers.get("should_rewrite")),
        "trigger_reasons": list(triggers.get("reasons", [])),
    }


def compute_handled(vault_dir: Path, sources_dir: Path) -> tuple[set[str], set[str]]:
    """Return (handled_slugs, handled_hashes) for vault files the ingest pipeline
    has already dealt with — source page, skip stub, or content-hash match.

    handled_slugs: stems of source pages + skip stubs (canonical slugs).
    handled_hashes: `source_hash:` values from source-page frontmatter (head-read).

    Hash extraction + the matcher itself live in `source_backlog` (shared with
    lint_wiki and the statusline). Slugs alone still drive the batch_first_slug
    self-heal (canonical-vs-canonical); the hash arm keeps the *count* robust to
    the spacey-vault-filename / canonical-source-slug split — under stem-only
    matching every such ingest became a permanent phantom (filed 2026-06-03).
    """
    source_slugs = {p.stem for p in sources_dir.glob("*.md")} if sources_dir.is_dir() else set()
    skipped_dir = vault_dir / ".skipped"
    skipped_slugs = {p.stem for p in skipped_dir.glob("*.md")} if skipped_dir.is_dir() else set()
    return source_slugs | skipped_slugs, collect_source_hashes(sources_dir)


def compute_pending(
    vault_dir: Path,
    sources_dir: Path,
    handled: tuple[set[str], set[str]] | None = None,
) -> int:
    """len() of the canonical source backlog — handled vs unhandled vault files.

    Delegates the match to source_backlog.compute_backlog so the count can never
    drift from lint_wiki's list or the statusline's reading.
    """
    if not vault_dir.is_dir():
        return 0
    if handled is None:
        handled = compute_handled(vault_dir, sources_dir)
    handled_slugs, handled_hashes = handled
    return len(compute_backlog(vault_dir, handled_slugs, handled_hashes))


def compute_backlog_state(project_root: Path, pending: int, handled: set[str] | None = None) -> dict:
    handled = handled or set()
    out: dict = {
        "pending": pending,
        "state": "all_clear",
        "triage_file": None,
        "triage_created": None,
        "batch_label": None,
        "batch_first_slug": None,
        "stale_count": 0,
    }
    if pending == 0:
        return out

    candidates = [
        p
        for p in (project_root / "dev").glob("ingest-triage-*.md")
        if "archived" not in p.name and "superseded" not in p.name
    ]
    candidates.sort(key=lambda p: p.name)
    if not candidates:
        out["state"] = "untriaged"
        return out

    latest = candidates[-1]
    out["triage_file"] = latest.relative_to(project_root).as_posix()
    try:
        text = latest.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        out["state"] = "untriaged"
        return out

    fm_match = re.search(r"\A---\n(.*?)\n---", text, re.DOTALL)
    fm_text = fm_match.group(1) if fm_match else ""
    status_match = TRIAGE_STATUS_RE.search(fm_text)
    if not status_match or status_match.group(1).lower() != "active":
        out["state"] = "untriaged"
        return out

    created_match = TRIAGE_CREATED_RE.search(fm_text)
    if created_match:
        out["triage_created"] = created_match.group(1)

    triaged_match = TRIAGED_COUNT_RE.search(text)
    triaged_count = int(triaged_match.group(1)) if triaged_match else pending
    if triaged_count < pending:
        out["state"] = "stale"
        out["stale_count"] = pending - triaged_count
        return out

    out["state"] = "triaged"
    next_batch = NEXT_BATCH_RE.search(text)
    if next_batch:
        out["batch_label"] = next_batch.group(1)
        batch_num = next_batch.group(2)
        # Find the `## Batch N — …` body section and extract the first numbered article.
        section_re = re.compile(
            rf"^## Batch {batch_num}\b.*?(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        section = section_re.search(text)
        if section:
            out["batch_first_slug"] = _first_open_article(section.group(0), handled)
    return out


def _first_open_article(batch_body: str, handled: set[str]) -> str | None:
    """First batch article that is neither ticked nor already on disk.

    An article is "open" when its checkbox is unchecked (`- [ ]`) AND its
    slug has no source page / skip stub yet. The on-disk check self-heals a
    missed tick — and lets pre-checkbox (legacy numbered) triage files still
    yield an honest pointer by falling back to the numbered format.
    """
    checkbox_re = re.compile(r"^- \[(?P<mark>[ xX])\]\s*\*\*`(?P<slug>[^`]+)`", re.MULTILINE)
    articles = [(m.group("mark").strip().lower(), m.group("slug")) for m in checkbox_re.finditer(batch_body)]
    if not articles:
        # Legacy numbered format (predates per-article checkboxes): treat all as unchecked.
        articles = [("", s) for s in re.findall(r"^\d+\.\s*\*\*`([^`]+)`", batch_body, re.MULTILINE)]
    for mark, slug in articles:
        if mark == "x":
            continue  # ticked → done
        slug_stem = slug[:-3] if slug.endswith(".md") else slug
        if slug_stem in handled:
            continue  # source page / skip stub exists → done even if tick was missed
        return slug
    return None


def build_state(wiki_root: Path, graph_json_arg: str | None) -> dict:
    project_root = wiki_root.parent
    vault_dir = project_root / "vault"
    sources_dir = wiki_root / "pages" / "sources"

    log_path = wiki_root / "log.md"
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.is_file() else ""

    lint_counter, ingest_count = compute_lint_state(log_text)
    overview = compute_overview_state(wiki_root, graph_json_arg, ingest_count)
    handled_slugs, handled_hashes = compute_handled(vault_dir, sources_dir)
    pending = compute_pending(vault_dir, sources_dir, (handled_slugs, handled_hashes))
    # Self-heal compares triage slugs (canonical) to source slugs (canonical), so
    # it takes the slug set only — never the (slugs, hashes) tuple.
    backlog = compute_backlog_state(project_root, pending, handled_slugs)
    # Mode 7 scale-audit re-fire detection. Own page walk (~0.3s); thresholds
    # come from .meta/scale-audit-config.json so a latched trigger stops nagging
    # once an audit resets the baseline. Drives the ⚙ SCALE AUDIT DUE banner.
    scale_audit = evaluate_scale_audit(wiki_root)

    return {
        "lint_counter": lint_counter,
        "lint_overdue": lint_counter >= LINT_OVERDUE_THRESHOLD,
        "ingest_count": ingest_count,
        "overview": overview,
        "backlog": backlog,
        "scale_audit": scale_audit,
    }


def emit_field_fast(wiki_root: Path, field: str) -> int:
    """Print one bare field value for the statusline (no JSON wrapping).

    `lint_counter` / `ingest_count` are log-only (instant). `pending` runs the
    canonical source-backlog matcher (a vault walk + a head-read of source-page
    frontmatter, ~35ms) — the statusline calls this instead of its old inline
    bash `comm`, so the count it shows can't drift from the drill or lint. It
    still skips the overview-trigger evaluation and graph build that the full
    JSON state needs.
    """
    if field == "pending":
        vault_dir = wiki_root.parent / "vault"
        sources_dir = wiki_root / "pages" / "sources"
        print(compute_pending(vault_dir, sources_dir))
        return 0
    log_path = wiki_root / "log.md"
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.is_file() else ""
    lint_counter, ingest_count = compute_lint_state(log_text)
    if field == "lint_counter":
        print(lint_counter)
    elif field == "ingest_count":
        print(ingest_count)
    else:
        print(f"error: unknown --field value '{field}'", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate wake-up-drill state (lint counter + overview + backlog) as JSON."
    )
    parser.add_argument("wiki_root", help="Path to the wiki root (e.g., wiki).")
    parser.add_argument(
        "--graph-json",
        help="Path to a precomputed graph_analyzer.py --json output, or '-' for stdin. "
        "Avoids re-rebuilding the graph when the caller already has it.",
    )
    parser.add_argument(
        "--field",
        choices=["lint_counter", "ingest_count", "pending"],
        help="Print one bare value (no JSON wrapping). Used by the statusline. "
        "lint_counter/ingest_count are log-only (instant); pending runs the shared "
        "source-backlog matcher (vault walk + frontmatter head-read).",
    )
    args = parser.parse_args(argv)

    wiki_root = Path(args.wiki_root).resolve()
    if not (wiki_root / "CLAUDE.md").is_file():
        print(
            f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)",
            file=sys.stderr,
        )
        return 2

    if args.field:
        return emit_field_fast(wiki_root, args.field)

    state = build_state(wiki_root, args.graph_json)
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
