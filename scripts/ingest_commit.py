#!/usr/bin/env python3
"""Close an ingest's write phase in one call: index, commit, log, backfills, lint.

Replaces the wiki-ingestor's steps 4–6 (`update_index.py`, `git add`,
`git commit`, `git log -1`, `append_log.py`, the telemetry backfills and the
mechanical `lint_wiki.py` run): five to nine Bash calls and hand-typed
Created/Updated lists become one call (script audit #8, s142).

    python ingest_commit.py <wiki> --source "vault/Web Articles/2026-04-15-foo.md" \
        --title "Source title" --paths pages/sources/2026-04-15-foo.md pages/concepts/bar.md \
        [--contradictions N] [--verify-decision slug=Y|P|N ...] \
        [--contradiction-outcome "slug-a|slug-b=a_wins|b_wins|both_stand|defer" ...] \
        [--trailer "Co-Authored-By: ..."] [--no-lint]

What it does, in order:
1. Regenerates the split indexes (`update_index.py`).
2. Classifies each `--paths` entry as created (untracked) or updated (tracked)
   *before* staging, so the lists are derived, never typed.
3. Stages exactly the write list + the index outputs + the raw source and
   commits **only those paths** (`git commit -- <paths>`), so unrelated changes
   the user had staged stay out of the ingest commit. Never `git add .`.
4. Appends the `log.md` entry with the real short hash.
5. Runs the post-commit telemetry backfills for any verify-clearing /
   contradiction-resolution decisions (post-commit on purpose: a
   late-contradiction rollback never reaches here, so no phantom stamps).
6. Runs `lint_wiki.py --log-mechanical --commit <hash>` and prints its summary.

The `log.md` entry and the mechanical `lint_log` row stay uncommitted; the
coordinator's `close_operation.py ingest` commits them with the telemetry row.

Output: a short header (`Commit:`, `Created:`, `Updated:`, per-step status),
then the lint summary. Exit codes: 0 = all done; 1 = nothing committed (bad
input, index or git failure; the working tree is as the agent left it);
3 = committed, but a post-commit step (log, backfill, lint) failed — the
header names it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
INDEX_OUTPUTS = ["pages/indexes", ".meta/categories-index.json", ".meta/index_stats.json"]
VERIFY_DECISIONS = {"Y", "P", "N"}
CONTRA_OUTCOMES = {"a_wins", "b_wins", "both_stand", "defer"}


class InputError(Exception):
    pass


def run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    # stdin=DEVNULL: an inherited invalid stdin handle makes Windows children fail with WinError 6 (s141).
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL)


def git(wiki: Path, *args: str) -> str:
    r = run(["git", "-C", str(wiki), *args])
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def script(name: str, *argv: str) -> subprocess.CompletedProcess:
    return run([sys.executable, str(SCRIPTS / name), *argv])


def normalize_paths(wiki: Path, raw: list) -> list:
    """Wiki-relative POSIX page paths, deduplicated in order; reject anything outside pages/."""
    out = []
    for p in raw:
        for part in p.split(","):
            part = part.strip().strip('"').replace("\\", "/")
            if not part:
                continue
            path = Path(part)
            if path.is_absolute():
                try:
                    part = path.resolve().relative_to(wiki).as_posix()
                except ValueError:
                    raise InputError(f"path outside the wiki: {p}")
            part = part.removeprefix("./")
            if part.startswith("wiki/"):
                part = part[len("wiki/"):]
            if not part.startswith("pages/") or part.startswith("pages/indexes/") or ".." in Path(part).parts:
                raise InputError(f"not a wiki page path (expected pages/<type>/<slug>.md): {p}")
            if not (wiki / part).is_file():
                raise InputError(f"no such page: {part}")
            if part not in out:
                out.append(part)
    if not out:
        raise InputError("--paths is empty")
    return out


def parse_pairs(raw: list, allowed: set, flag: str) -> list:
    out = []
    for item in raw:
        key, sep, val = item.rpartition("=")
        key, val = key.strip(), val.strip()
        if not sep or not key or val not in allowed:
            raise InputError(f"{flag} {item!r}: expected <key>=<{'|'.join(sorted(allowed))}>")
        out.append((key, val))
    return out


def source_abspath(wiki: Path, source: str) -> Path:
    top = Path(git(wiki, "rev-parse", "--show-toplevel").strip())
    src = Path(source.strip().strip('"'))
    src = src if src.is_absolute() else top / src
    if not src.is_file():
        raise InputError(f"no such source file: {source}")
    return src.resolve()


def slugs(paths: list) -> str:
    return ", ".join(Path(p).stem for p in paths)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="Index + commit + log + backfills + mechanical lint for one ingest.")
    ap.add_argument("wiki", help="Path to the wiki root")
    ap.add_argument("--source", required=True, help="Raw vault file, project-relative (vault/...) or absolute")
    ap.add_argument("--title", required=True, help="Source title (commit subject and log entry)")
    ap.add_argument("--paths", nargs="+", required=True,
                    help="Every page written this ingest (WRITE_LIST), wiki-relative; commas also accepted")
    ap.add_argument("--contradictions", type=int, default=0, help="Contradictions recorded (plan + late-found)")
    ap.add_argument("--verify-decision", action="append", default=[], metavar="SLUG=Y|P|N")
    ap.add_argument("--contradiction-outcome", action="append", default=[], metavar="A|B=OUTCOME")
    ap.add_argument("--trailer", action="append", default=[], help="Commit trailer line, e.g. Co-Authored-By")
    ap.add_argument("--no-lint", action="store_true", help="Skip the mechanical lint (tests, dry runs)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp1252 consoles
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    wiki = Path(a.wiki).resolve()
    if not (wiki / "CLAUDE.md").is_file() and not (wiki / "AGENTS.md").is_file():
        print(f"error: {wiki} does not look like a wiki root", file=sys.stderr)
        return 1

    # ---- pre-commit: validate, index, classify, stage, commit (any failure = nothing committed)
    try:
        paths = normalize_paths(wiki, a.paths)
        verify = parse_pairs(a.verify_decision, VERIFY_DECISIONS, "--verify-decision")
        contra = [("|".join(sorted(s.strip() for s in k.split("|"))), v)  # log pair_keys are sorted
                  for k, v in parse_pairs(a.contradiction_outcome, CONTRA_OUTCOMES, "--contradiction-outcome")]
        src = source_abspath(wiki, a.source)

        r = script("update_index.py", str(wiki))
        if r.returncode != 0:
            raise RuntimeError(f"update_index.py exited {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}")

        tracked = set(git(wiki, "ls-files", "--", *paths).split())
        created = [p for p in paths if p not in tracked]
        updated = [p for p in paths if p in tracked]

        index_paths = [p for p in INDEX_OUTPUTS if (wiki / p).exists()]
        stage = paths + index_paths + [str(src)]
        git(wiki, "add", "--", *stage)
        if not git(wiki, "diff", "--cached", "--name-only", "--", *stage).strip():
            raise RuntimeError("nothing to commit: the listed pages have no changes")

        body = []
        if created:
            body += ["-m", f"- Created: {slugs(created)}"]
        if updated:
            body += ["-m", f"- Updated: {slugs(updated)}"]
        if a.trailer:
            body += ["-m", "\n".join(t.strip() for t in a.trailer if t.strip())]
        git(wiki, "commit", "-m", f"ingest: {a.title}", *body, "--", *stage)
        commit = git(wiki, "rev-parse", "--short", "HEAD").strip()
    except (InputError, RuntimeError) as e:
        print(f"ingest_commit: FAILED before commit — {e}\nNothing committed.", file=sys.stderr)
        return 1

    # ---- post-commit: log, backfills, lint (failures reported, never undo the commit)
    status = []
    failed = False
    r = script("append_log.py", "ingest", "--title", a.title,
               "--created", ",".join(Path(p).stem for p in created),
               "--updated", ",".join(Path(p).stem for p in updated),
               "--contradictions", str(a.contradictions), "--commit", commit, "--wiki", str(wiki))
    if r.returncode == 0:
        status.append("Log: appended")
    else:
        failed = True
        status.append(f"Log: ERROR {(r.stderr or r.stdout).strip()[:200]}")

    for slug, decision in verify:
        r = script("append_corroborate_log.py", "--wiki", str(wiki), "--backfill-ingest",
                   "--page-slug", slug, "--decision", decision)
        out = (r.stdout or r.stderr).strip().splitlines()
        if r.returncode == 0:
            note = "skipped (no corroborate_log row)" if out and out[-1].startswith("skipped") else "stamped"
            status.append(f"Verify-clearing: {slug} — {decision} ({note})")
        else:
            failed = True
            status.append(f"Verify-clearing: {slug} — {decision} (ERROR: {' '.join(out)[:200]})")

    for pair, outcome in contra:
        r = script("append_contradiction_log.py", "--wiki", str(wiki), "--backfill-ingest",
                   "--pair-key", pair, "--outcome", outcome)
        out = (r.stdout or r.stderr).strip().splitlines()
        if r.returncode == 0:
            status.append(f"Contradiction-resolution: {pair} — {outcome} (stamped)")
        else:
            # No matching row is the normal case for an edge the ingest merely touched.
            status.append(f"Contradiction-resolution: {pair} — {outcome} (not stamped: {' '.join(out)[:200]})")

    lint_out = ""
    if a.no_lint:
        status.append("Lint: skipped (--no-lint)")
    else:
        r = script("lint_wiki.py", str(wiki), "--log-mechanical", "--commit", commit)
        lint_out = r.stdout.strip()
        if r.returncode != 0 or not lint_out:
            failed = True
            status.append(f"Lint: ERROR exit {r.returncode}: {r.stderr.strip()[:200]}")
        else:
            status.append("Lint: row logged")

    print(f"ingest_commit: {'PARTIAL' if failed else 'ok'}")
    print(f"Commit: {commit}")
    print(f"Created: {slugs(created) or 'none'}")
    print(f"Updated: {slugs(updated) or 'none'}")
    for line in status:
        print(line)
    if lint_out:
        print("\n--- mechanical lint ---")
        print(lint_out)
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
