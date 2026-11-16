#!/usr/bin/env python3
"""Close a wiki operation: parse the agent result, log telemetry, commit it.

One closer for every dispatching skill (s137, script audit Wave 2). It
replaces the hand-run tail each SKILL used to carry: regexing the agent's
footer, piping `<usage>` blocks to the parser, building appender commands,
the separate telemetry commit, and (ingest) the triage checkbox tick.

    close_operation.py ingest    --wiki W --agent-id ID [--brief B | --source S]  < result.txt
    close_operation.py lint      --wiki W --agent-id ID [--agent-id ID2]          < result.txt
    close_operation.py overview  --wiki W --agent-id ID --mode soft|full --status S [--commit H] [--trigger T] < usage.txt
    close_operation.py query     --wiki W --query Q --search-mode M [--agent-id ID] [...] < result.txt
    close_operation.py summarize --wiki W --query Q --search-mode M --agent-id ID [...]   < result.txt
    close_operation.py graph     --wiki W --query "neighbors X" --pages-consulted a,b --duration-ms N
    close_operation.py ask       --wiki W --query Q --routed-to T --duration-ms N

stdin: every Agent tool result (or completion notification) for the
operation, verbatim: the agent's report AND its `<usage>` block(s). The
closer finds the blocks itself (`parse_agent_usage`) and enriches them from
the subagent transcript via `--agent-id`; when no block arrived, duration
falls back to the transcript's turn spans. `--duration-ms` still overrides.
Use a quoted heredoc so nothing in the report is shell-expanded:

    python close_operation.py ingest --wiki wiki --agent-id a1b2 --brief .claude/tmp/ingest-brief-foo.json <<'RESULT'
    <agent result text, verbatim, including <usage>...</usage>>
    RESULT

Rules it enforces (each one an incident class):
- **Never amend.** Telemetry gets its own commit (s132: amends orphaned the
  recorded hashes). Explicit paths only; a no-change tree commits nothing.
- **Strict footers.** A lint footer missing a required field writes nothing
  and exits 2 (s75: a misclassified row broke the lint counter). An ingest
  result without `Commit:` exits 2 (nothing to attribute the row to).
- **Slugs from git.** Ingest created/updated slugs come from
  `git show --name-status` on the reported commit, unioned with the report's
  lists, as bare stems (90/299 old rows carried `sources/` prefixes).
- **Telemetry is observational.** An appender failure is reported in
  `warnings`, never raised; the exit code stays 0.

Output: one compact JSON object on stdout (`op`, `row`, `telemetry_commit`,
op-specific fields, `warnings`). Stdlib only; Python 3.9-safe.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import parse_frontmatter  # noqa: E402
from parse_agent_usage import enrich, parse_usage_text  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPTS = Path(__file__).resolve().parent
COST_FLAGS = (
    ("--total-tokens", "total_tokens"),
    ("--input-tokens", "input_tokens"),
    ("--cache-creation-input-tokens", "cache_creation_input_tokens"),
    ("--cache-read-input-tokens", "cache_read_input_tokens"),
    ("--output-tokens", "output_tokens"),
    ("--tool-uses", "tool_uses"),
)
VALID_SOURCE_TYPES = {"article", "paper", "gist", "blog", "docs", "transcript", "notes", "other"}
INGEST_LINE_RE = re.compile(r"^## \[.*?\] ingest \|", re.MULTILINE)
LINT_FOOTER_FIELDS = ("Ingests at run", "Total pages", "Verdict", "Verdict-summary", "Counts", "Fixes committed")


class FooterError(Exception):
    pass


# ---------------------------------------------------------------- helpers


def git(wiki: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(wiki), *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def commit_paths(repo_dir: Path, paths: list, message: str) -> str | None:
    """Stage exactly `paths` (relative to repo_dir) and commit. None if nothing changed."""
    paths = [p for p in paths if (repo_dir / p).exists()]
    if not paths:
        return None
    if not git(repo_dir, "status", "--porcelain", "--", *paths).strip():
        return None
    git(repo_dir, "add", "--", *paths)
    git(repo_dir, "commit", "-m", message, "--", *paths)
    return git(repo_dir, "rev-parse", "--short", "HEAD").strip()


def ingest_count(wiki: Path) -> int:
    log = wiki / "log.md"
    return len(INGEST_LINE_RE.findall(log.read_text(encoding="utf-8"))) if log.is_file() else 0


def footer_value(text: str, key: str) -> str | None:
    """Last `Key: value` line in text (footers come last; reports may quote earlier ones)."""
    hits = re.findall(rf"^\s*{re.escape(key)}:[ \t]*(.*?)\s*$", text, re.MULTILINE)
    return hits[-1] if hits else None


SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def stem(entry: str) -> str:
    entry = entry.strip().strip("`").strip()
    entry = re.sub(r"^\[\[|\]\]$", "", entry)
    return Path(entry.split("|")[0].split("#")[0]).stem if entry else ""


def report_list(text: str, header: str) -> list:
    """Bullets under a `Header:` line, up to the next blank/non-bullet line."""
    m = re.search(rf"^{header}:\s*\n((?:[ \t]*[-*] .*\n?)+)", text, re.MULTILINE)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        # Bullets come as `path — note`, `path (note)` or `path: note` (s138 live run);
        # cut at the first separator, then keep only slug-shaped stems.
        entry = re.split(r" — |: | \(", re.sub(r"^[ \t]*[-*] ", "", line), maxsplit=1)[0]
        s = stem(entry)
        if SLUG_RE.fullmatch(s) and s not in out:
            out.append(s)
    return out


def usage_from(text: str, agent_ids: list, cwd: str, projects_root: str | None) -> dict:
    usage = parse_usage_text(text)
    if agent_ids:
        enrich(usage, agent_ids, cwd, projects_root)
    return usage


def cost_argv(usage: dict, duration_override: int | None, warnings: list) -> list:
    argv: list = []
    for flag, field in COST_FLAGS:
        if usage.get(field) is not None:
            argv += [flag, str(usage[field])]
    duration = duration_override if duration_override is not None else usage.get("duration_ms")
    if duration is None:
        warnings.append("no duration from <usage>, transcript or --duration-ms; logged 0")
        duration = 0
    return argv + ["--duration-ms", str(int(duration))]


def run_appender(script: str, argv: list, warnings: list) -> bool:
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *argv], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        warnings.append(f"{script} exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")
        return False
    return True


def read_stdin(enabled: bool) -> str:
    if not enabled or sys.stdin is None or sys.stdin.isatty():
        return ""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")  # Windows defaults to cp1252: "→" arrived as "â†’" (s138)
    return sys.stdin.read()


# ---------------------------------------------------------------- ingest


def git_changed_pages(wiki: Path, commit: str) -> tuple[list, list]:
    out = git(wiki, "show", "--relative", "--name-status", "--format=", commit)
    created, updated = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if not path.startswith("pages/") or path.startswith("pages/indexes/") or not path.endswith(".md"):
            continue
        slug = Path(path).stem
        if status[0] in "AR" and slug not in created:
            created.append(slug)
        elif status[0] == "M" and slug not in updated:
            updated.append(slug)
    return created, updated


def source_type_of(source: Path, project_root: Path, warnings: list) -> str:
    path = source if source.is_absolute() else project_root / source
    try:
        fm = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        fm = {}
    st = str(fm.get("source_type") or "").strip().lower()
    if st in VALID_SOURCE_TYPES:
        return st
    guess = "paper" if "research papers" in str(source).lower() else "article"
    warnings.append(f"source_type {st or 'missing'!r} not in vocabulary; logged {guess!r}")
    return guess


def triage_tick(project_root: Path, slug: str) -> dict | None:
    """Tick `slug` in the active triage file (+ batch roll-up) and commit. None if not a batch article."""
    dev = project_root / "dev"
    candidates = sorted(
        p for p in dev.glob("ingest-triage-*.md") if "archived" not in p.name and "superseded" not in p.name
    ) if dev.is_dir() else []
    if not candidates:
        return None
    triage = candidates[-1]
    text = triage.read_text(encoding="utf-8")
    fm = re.search(r"\A---\n(.*?)\n---", text, re.DOTALL)
    if not fm or not re.search(r"^status:\s*active\b", fm.group(1), re.MULTILINE | re.IGNORECASE):
        return None
    line_re = re.compile(rf"^- \[ \] \*\*`{re.escape(slug)}(?:\.md)?`\*\*", re.MULTILINE)
    m = line_re.search(text)
    if not m:
        return None
    text = text[: m.start()] + "- [x]" + text[m.start() + 5:]

    result = {"file": triage.relative_to(project_root).as_posix(), "slug": slug,
              "batch": None, "done": None, "total": None, "batch_complete": False, "all_complete": False}
    # The `## Batch N` section holding the article.
    sections = list(re.finditer(r"^## Batch (\d+)\b.*?(?=^## |\Z)", text, re.MULTILINE | re.DOTALL))
    for sec in sections:
        if sec.start() <= m.start() < sec.end():
            body = sec.group(0)
            n = sec.group(1)
            done = len(re.findall(r"^- \[[xX]\] \*\*`", body, re.MULTILINE))
            total = done + len(re.findall(r"^- \[ \] \*\*`", body, re.MULTILINE))
            result.update(batch=int(n), done=done, total=total)
            if done == total:
                text = re.sub(rf"^- \[ \] (\*\*Batch {n}\*\*)", r"- [x] \1", text, count=1, flags=re.MULTILINE)
                result["batch_complete"] = True
            break
    result["all_complete"] = not re.search(r"^- \[ \] \*\*Batch \d+\*\*", text, re.MULTILINE)
    triage.write_text(text, encoding="utf-8")

    label = f"Batch {result['batch']} — {result['done']}/{result['total']}" if result["batch"] else "no batch"
    result["commit"] = commit_paths(project_root, [result["file"]], f"triage: tick {slug} ({label})")
    return result


def close_ingest(a, wiki: Path, text: str, warnings: list) -> dict:
    project_root = Path(git(wiki, "rev-parse", "--show-toplevel").strip())
    brief = {}
    if a.brief:
        brief = json.loads(Path(a.brief).read_text(encoding="utf-8"))
    source = a.source or brief.get("source_path")
    if not source:
        raise FooterError("need --brief or --source")
    commit = footer_value(text, "Commit")
    commit = commit.split()[0] if commit else None
    if not commit or not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        raise FooterError("ingest result has no `Commit: <hash>` line (HALT/rollback?); nothing logged")
    git(wiki, "cat-file", "-e", f"{commit}^{{commit}}")

    created, updated = git_changed_pages(wiki, commit)
    for s in report_list(text, "Created"):
        if s not in created and s not in updated:
            created.append(s)
    for s in report_list(text, "Updated"):
        if s not in created and s not in updated:
            updated.append(s)

    contra = footer_value(text, "Contradictions recorded")
    contra_n = int(re.match(r"\d+", contra).group(0)) if contra and re.match(r"\d+", contra) else 0
    words = brief.get("word_count")
    if words is None:
        path = Path(source) if Path(source).is_absolute() else project_root / source
        words = len(path.read_text(encoding="utf-8", errors="replace").split())

    usage = usage_from(text, a.agent_id, a.cwd, a.projects_root)
    argv = ["--wiki", str(wiki), "--source-path", str(source).replace("\\", "/"),
            "--source-type", a.source_type or source_type_of(Path(source), project_root, warnings),
            "--source-word-count", str(words),
            "--pages-created", ",".join(created), "--pages-updated", ",".join(updated),
            "--contradictions-flagged", str(contra_n), "--commit-hash", commit]
    argv += cost_argv(usage, a.duration_ms, warnings)
    ok = run_appender("append_ingest_log.py", argv, warnings)
    tcommit = commit_paths(wiki, [".meta/ingest_log.jsonl", "log.md", ".meta/lint_log.jsonl",
                            # rows stamped by ingest_commit.py --verify-decision / --contradiction-outcome
                            ".meta/corroborate_log.jsonl", ".meta/contradiction_log.jsonl"],
                           f"ingest: telemetry for {commit}")

    slug = brief.get("suggested_slug") or Path(source).stem
    triage = None
    if not a.no_triage:
        try:
            triage = triage_tick(project_root, slug)
        except (OSError, RuntimeError) as e:
            warnings.append(f"triage tick failed: {e}")
    if a.brief and ok:
        try:
            os.remove(a.brief)
        except OSError:
            pass
    return {"op": "ingest", "row": ok, "content_commit": commit, "telemetry_commit": tcommit,
            "created": created, "updated": updated, "contradictions": contra_n,
            "usage": _usage_summary(usage), "triage": triage}


# ---------------------------------------------------------------- lint


def parse_lint_footer(text: str) -> dict:
    vals = {k: footer_value(text, k) for k in LINT_FOOTER_FIELDS}
    missing = [k for k, v in vals.items() if v in (None, "")]
    if missing:
        raise FooterError(f"lint footer missing: {', '.join(missing)}; nothing logged")
    try:
        counts = json.loads(vals["Counts"])
        ingests, pages = int(vals["Ingests at run"]), int(vals["Total pages"])
    except (ValueError, TypeError) as e:
        raise FooterError(f"lint footer malformed ({e}); nothing logged")
    verdict = vals["Verdict"].lower()
    if verdict not in ("clean", "issues"):
        raise FooterError(f"lint footer Verdict {vals['Verdict']!r} not clean|issues; nothing logged")
    fix = vals["Fixes committed"].split()[0]
    fix = "" if fix.lower() == "none" else fix
    return {"ingests": ingests, "pages": pages, "verdict": verdict, "summary": vals["Verdict-summary"],
            "counts": counts, "fix": fix}


def close_lint(a, wiki: Path, text: str, warnings: list) -> dict:
    f = parse_lint_footer(text)
    usage = usage_from(text, a.agent_id, a.cwd, a.projects_root)
    ok_log = run_appender("append_log.py", ["lint", "--title", f["summary"], "--commit", f["fix"],
                                            "--wiki", str(wiki)], warnings)
    argv = ["--wiki", str(wiki), "--lint-type", "full", "--ingest-count-at-run", str(f["ingests"]),
            "--total-pages", str(f["pages"]), "--counts", json.dumps(f["counts"]),
            "--verdict", f["verdict"], "--semantic-pass-ran", "true", "--commit-hash", f["fix"]]
    argv += cost_argv(usage, a.duration_ms, warnings)
    ok_row = run_appender("append_lint_log.py", argv, warnings)
    msg = (f"lint: telemetry for {f['fix']}" if f["fix"]
           else f"lint: {f['summary']} at ingest #{f['ingests']} — {f['pages']} pages")
    tcommit = commit_paths(wiki, ["log.md", ".meta/lint_log.jsonl"], msg)
    return {"op": "lint", "row": ok_row, "log_entry": ok_log, "fix_commit": f["fix"] or None,
            "telemetry_commit": tcommit, "verdict": f["verdict"], "ingest_count": f["ingests"],
            "usage": _usage_summary(usage)}


# ---------------------------------------------------------------- overview


def close_overview(a, wiki: Path, text: str, warnings: list) -> dict:
    n = a.ingest_count if a.ingest_count is not None else ingest_count(wiki)
    usage = usage_from(text, a.agent_id, a.cwd, a.projects_root)
    argv = ["--wiki", str(wiki), "--mode", a.mode, "--ingest-count-at-run", str(n),
            "--status", a.status, "--commit-hash", a.commit or ""]
    if a.trigger:
        argv += ["--trigger", a.trigger]
    argv += cost_argv(usage, a.duration_ms, warnings)
    ok = run_appender("append_overview_log.py", argv, warnings)
    what = "soft refresh" if a.mode == "soft" else "full rewrite"
    tcommit = commit_paths(wiki, [".meta/overview_log.jsonl"], f"overview: telemetry for {what} at ingest #{n}")
    return {"op": "overview", "row": ok, "telemetry_commit": tcommit, "ingest_count": n,
            "usage": _usage_summary(usage)}


# ---------------------------------------------------------------- query / summarize / ask


def parse_filed_as(text: str) -> str:
    v = footer_value(text, "Filed as")
    if not v or v.lower().startswith("not filed"):
        return ""
    return stem(v.split()[0])


def close_query(a, wiki: Path, text: str, warnings: list) -> dict:
    pages = a.pages_consulted
    if pages is None:
        line = footer_value(text, "Pages consulted") or ""
        pages = ",".join(dict.fromkeys(stem(s) for s in re.findall(r"\[\[([^\]]+)\]\]", line)))
    filed = a.filed_as if a.filed_as is not None else parse_filed_as(text)
    agent_ids = getattr(a, "agent_id", [])
    usage = usage_from(text, agent_ids, a.cwd, a.projects_root) if (agent_ids or "<usage>" in text) else {}
    argv = ["--wiki", str(wiki), "--query-skill", a.op, "--query", a.query,
            "--pages-consulted", pages, "--top-score", "" if a.top_score is None else str(a.top_score),
            "--filed-as", filed]
    if a.search_mode:
        argv += ["--search-mode", a.search_mode]
    if a.query_variations:
        argv += ["--query-variations", a.query_variations]
    if a.routed_from:
        argv += ["--routed-from", a.routed_from]
    argv += cost_argv(usage, a.duration_ms, warnings)
    ok = run_appender("append_query_log.py", argv, warnings)
    tcommit = commit_paths(wiki, [".meta/query_log.jsonl"], f"{a.op}: telemetry")
    return {"op": a.op, "row": ok, "telemetry_commit": tcommit, "pages_consulted": pages.split(",") if pages else [],
            "filed_as": filed or None, "usage": _usage_summary(usage)}


def close_ask(a, wiki: Path, text: str, warnings: list) -> dict:
    argv = ["--wiki", str(wiki), "--query-skill", "ask", "--query", a.query, "--routed-to", a.routed_to,
            "--routing-clarifications", str(a.routing_clarifications), "--duration-ms", str(a.duration_ms)]
    ok = run_appender("append_query_log.py", argv, warnings)
    tcommit = commit_paths(wiki, [".meta/query_log.jsonl"], "ask: telemetry")
    return {"op": "ask", "row": ok, "telemetry_commit": tcommit}


def _usage_summary(usage: dict) -> dict:
    keys = ("input_tokens", "cache_creation_input_tokens", "output_tokens", "tool_uses", "duration_ms")
    out = {k: usage.get(k) for k in keys if usage.get(k) is not None}
    if all(usage.get(k) is not None for k in keys[:3]):
        out["non_cached"] = sum(usage[k] for k in keys[:3])
    if usage.get("models"):
        out["models"] = usage["models"]
    if usage.get("duration_source"):
        out["duration_source"] = usage["duration_source"]
    return out


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="op", required=True)

    def common(sp, agent=True):
        sp.add_argument("--wiki", required=True, help="Wiki root")
        if agent:
            sp.add_argument("--agent-id", action="append", default=[], help="Subagent id (repeatable)")
            sp.add_argument("--duration-ms", type=int, default=None, help="Override the measured duration")
            sp.add_argument("--cwd", default=os.getcwd(), help=argparse.SUPPRESS)
            sp.add_argument("--projects-root", default=None, help=argparse.SUPPRESS)

    sp = sub.add_parser("ingest", help="ingestor result -> ingest_log row, telemetry commit, triage tick")
    common(sp)
    sp.add_argument("--brief", help="Brief JSON from ingest_preflight.py (deleted after a logged row)")
    sp.add_argument("--source", help="Raw vault path (when no --brief)")
    sp.add_argument("--source-type", default=None, help="Override the frontmatter source_type")
    sp.add_argument("--no-triage", action="store_true", help="Skip the triage checkbox tick")

    sp = sub.add_parser("lint", help="linter result -> log.md entry + lint_log row + telemetry commit")
    common(sp)

    sp = sub.add_parser("overview", help="curator usage -> overview_log row + telemetry commit")
    common(sp)
    sp.add_argument("--mode", required=True, choices=["soft", "full"])
    sp.add_argument("--status", required=True, choices=["applied", "no_changes", "cancelled"])
    sp.add_argument("--commit", default="", help="Content commit hash ('' when none)")
    sp.add_argument("--trigger", default=None, help="Full mode: trigger reasons or 'user-invoked'")
    sp.add_argument("--ingest-count", type=int, default=None, help="Default: counted from log.md")

    for name in ("query", "summarize"):
        sp = sub.add_parser(name, help=f"/wiki-{name} -> query_log row + telemetry commit")
        common(sp)
        sp.add_argument("--query", required=True)
        sp.add_argument("--search-mode", required=True)
        sp.add_argument("--top-score", type=float, default=None)
        sp.add_argument("--pages-consulted", default=None, help="Comma slugs; default parsed from the footer")
        sp.add_argument("--filed-as", default=None, help="Synthesis slug; default parsed from the footer")
        sp.add_argument("--query-variations", default=None, help="JSON array string")
        sp.add_argument("--routed-from", choices=["ask"], default=None)

    sp = sub.add_parser("graph", help="/wiki-graph row (no model cost) + telemetry commit")
    common(sp, agent=False)
    sp.add_argument("--cwd", default=os.getcwd(), help=argparse.SUPPRESS)
    sp.add_argument("--projects-root", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--query", required=True, help="Raw subcommand + args, e.g. 'neighbors rag --depth 2'")
    sp.add_argument("--pages-consulted", required=True, help="Result slugs (subgraph: capped, +N-more sentinel)")
    sp.add_argument("--duration-ms", type=int, required=True)
    sp.add_argument("--routed-from", choices=["ask"], default=None)
    sp.set_defaults(search_mode=None, top_score=None, filed_as="", query_variations=None)

    sp = sub.add_parser("ask", help="/wiki-ask router row + telemetry commit")
    common(sp, agent=False)
    sp.add_argument("--query", required=True)
    sp.add_argument("--routed-to", required=True, choices=["query", "graph", "summarize", "trace"])
    sp.add_argument("--routing-clarifications", type=int, default=0)
    sp.add_argument("--duration-ms", type=int, required=True)
    return p


HANDLERS = {"ingest": close_ingest, "lint": close_lint, "overview": close_overview,
            "query": close_query, "summarize": close_query, "graph": close_query, "ask": close_ask}


def main(argv: list, stdin_text: str | None = None) -> int:
    a = build_parser().parse_args(argv)
    wiki = Path(a.wiki).resolve()
    if not (wiki / "CLAUDE.md").is_file():
        print(json.dumps({"op": a.op, "error": f"{wiki} is not a wiki root (no CLAUDE.md)"}))
        return 2
    text = stdin_text if stdin_text is not None else read_stdin(a.op not in ("ask", "graph"))
    warnings: list = []
    try:
        out = HANDLERS[a.op](a, wiki, text, warnings)
    except FooterError as e:
        print(json.dumps({"op": a.op, "error": str(e)}, ensure_ascii=False))
        return 2
    except RuntimeError as e:
        print(json.dumps({"op": a.op, "error": str(e)}, ensure_ascii=False))
        return 2
    out["warnings"] = warnings
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
