"""Tests for close_operation.py and ingest_preflight.py (s137, script audit Wave 2).

close_operation: ingest row + slugs from git + separate telemetry commit +
triage tick; strict lint footer; overview row; query footer parse; ask row.
ingest_preflight: exclusion guard (never renames protocol files), single-file
rename, duplicate detection, model pick, triage lookup.
parse_agent_usage: transcript span fills a missing duration.

All git work happens in throwaway repos under a temp dir.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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

import close_operation  # noqa: E402
import ingest_preflight  # noqa: E402
import parse_agent_usage  # noqa: E402

TRIAGE = """---
status: active
created: 2026-11-01
---

# Triage

## Status checklist
- [ ] **Batch 1** — 2 ingests (agents)
- [ ] **Batch 2** — 1 ingests (misc)

## Batch 1 — agents
- [x] **`2026-11-01-done.md`** — already in
- [ ] **`2026-11-02-new-source.md`** — this one

## Batch 2 — misc
- [ ] **`2026-11-03-other.md`** — later
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


class Repo:
    """Project root with vault/, wiki/ and dev/, committed."""

    def __init__(self, tmp: Path):
        self.root = tmp
        self.wiki = tmp / "wiki"
        for d in ("pages/sources", "pages/concepts", "pages/indexes", ".meta"):
            (self.wiki / d).mkdir(parents=True)
        (self.wiki / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (self.wiki / "log.md").write_text("# Log\n\n## [2026-11-01] ingest | old\n", encoding="utf-8")
        (self.wiki / "pages/concepts/agents.md").write_text("---\ntitle: Agents\n---\nbody\n", encoding="utf-8")
        (tmp / "vault/Clippings").mkdir(parents=True)
        (tmp / "vault/Clippings/2026-11-02-new-source.md").write_text(
            "---\ntitle: New Source\nsource_type: blog\n---\n" + "word " * 50, encoding="utf-8")
        (tmp / "dev").mkdir()
        git(tmp, "init", "-q")
        git(tmp, "config", "user.email", "t@example.invalid")
        git(tmp, "config", "user.name", "Test")
        git(tmp, "config", "commit.gpgsign", "false")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-q", "-m", "init")

    def fake_ingest(self) -> str:
        (self.wiki / "pages/sources/2026-11-02-new-source.md").write_text("---\nsource_hash: abc\n---\n", encoding="utf-8")
        (self.wiki / "pages/indexes/index.md").write_text("idx\n", encoding="utf-8")
        with (self.wiki / "pages/concepts/agents.md").open("a", encoding="utf-8") as f:
            f.write("more\n")
        with (self.wiki / "log.md").open("a", encoding="utf-8") as f:
            f.write("\n## [2026-11-15] ingest | New Source\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "ingest: New Source")
        return git(self.root, "rev-parse", "--short", "HEAD").strip()


def run_close(argv, text=""):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = close_operation.main(argv, stdin_text=text)
    return code, json.loads(buf.getvalue())


def rows(wiki: Path, name: str) -> list:
    p = wiki / ".meta" / name
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()] if p.exists() else []


class TestCloseIngest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_row_slugs_and_separate_commit(self):
        h = self.repo.fake_ingest()
        report = (f"Ingest complete: New Source\n\nCreated:\n- pages/sources/2026-11-02-new-source.md\n\n"
                  f"Updated:\n- pages/concepts/agents.md\n\nContradictions recorded: 2  (plan: 2, late-found: 0)\n"
                  f"Commit: {h}\n<usage><subagent_tokens>5000</subagent_tokens><tool_uses>9</tool_uses>"
                  f"<duration_ms>60000</duration_ms></usage>")
        code, out = run_close(["ingest", "--wiki", str(self.repo.wiki),
                               "--source", "vault/Clippings/2026-11-02-new-source.md", "--no-triage"], report)
        self.assertEqual(code, 0, out)
        self.assertTrue(out["row"], out)
        row = rows(self.repo.wiki, "ingest_log.jsonl")[-1]
        self.assertEqual(row["pages_created"], ["2026-11-02-new-source"])
        self.assertEqual(row["pages_updated"], ["agents"])  # index excluded
        self.assertEqual(row["contradictions_flagged"], 2)
        self.assertEqual(row["source_type"], "blog")
        self.assertEqual(row["source_word_count"], 50 + 7)  # no brief: whole-file split, frontmatter included
        self.assertEqual((row["duration_ms"], row["tool_uses"], row["commit_hash"]), (60000, 9, h))
        # The content commit is untouched (no amend) and telemetry sits on top of it.
        log = git(self.repo.root, "log", "--format=%h %s", "-3").splitlines()
        self.assertTrue(log[0].endswith(f"ingest: telemetry for {h}"))
        self.assertTrue(log[1].startswith(h))
        self.assertEqual(git(self.repo.root, "status", "--porcelain"), "")

    def test_report_list_prose_bullets(self):
        # s138 live run: `path: description` bullets leaked prose into pages_updated.
        report = ("Updated:\n"
                  "- C:/Projects/X/wiki/pages/concepts/agents-md.md: the \"decisions\" section. Confidence 0.87 → 0.88.\n"
                  "- C:/Projects/X/wiki/pages/concepts/goal.md: under \"Distinction from external/pattern-level loops\", x\n"
                  "- [[claude-code-skills]] — added rules\n"
                  "- pages/concepts/ralph-loop.md (new section)\n"
                  "- Seb has no page, so plain text\n")
        self.assertEqual(close_operation.report_list(report, "Updated"),
                         ["agents-md", "goal", "claude-code-skills", "ralph-loop"])

    def test_missing_commit_line_logs_nothing(self):
        code, out = run_close(["ingest", "--wiki", str(self.repo.wiki),
                               "--source", "vault/Clippings/2026-11-02-new-source.md"],
                              "HALT: 7 late contradictions; rolled back")
        self.assertEqual(code, 2)
        self.assertIn("Commit", out["error"])
        self.assertEqual(rows(self.repo.wiki, "ingest_log.jsonl"), [])

    def test_brief_and_triage_tick(self):
        triage = self.repo.root / "dev/ingest-triage-2026-11-01.md"
        triage.write_text(TRIAGE, encoding="utf-8")
        git(self.repo.root, "add", "-A")
        git(self.repo.root, "commit", "-q", "-m", "triage")
        brief = self.repo.root / "brief.json"
        brief.write_text(json.dumps({"source_path": "vault/Clippings/2026-11-02-new-source.md",
                                     "word_count": 1234, "suggested_slug": "2026-11-02-new-source"}), encoding="utf-8")
        h = self.repo.fake_ingest()
        code, out = run_close(["ingest", "--wiki", str(self.repo.wiki), "--brief", str(brief),
                               "--duration-ms", "42"], f"Commit: {h}\n")
        self.assertEqual(code, 0, out)
        self.assertFalse(brief.exists(), "brief is consumed after a logged row")
        self.assertEqual(rows(self.repo.wiki, "ingest_log.jsonl")[-1]["source_word_count"], 1234)
        t = out["triage"]
        self.assertEqual((t["batch"], t["done"], t["total"], t["batch_complete"], t["all_complete"]),
                         (1, 2, 2, True, False))
        text = triage.read_text(encoding="utf-8")
        self.assertIn("- [x] **`2026-11-02-new-source.md`**", text)
        self.assertIn("- [x] **Batch 1**", text)
        self.assertIn("- [ ] **Batch 2**", text)
        self.assertIn("triage: tick 2026-11-02-new-source (Batch 1 — 2/2)",
                      git(self.repo.root, "log", "-1", "--format=%s"))


class TestCloseLint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    FOOTER = ("Report...\n---\nIngests at run: 12\nTotal pages: 40\nVerdict: issues\n"
              "Verdict-summary: 2 flags, no fixes applied\nCounts: {\"orphans\": 2}\nFixes committed: none\n"
              "<usage><subagent_tokens>9</subagent_tokens><duration_ms>1000</duration_ms></usage>")

    def test_no_fix_branch(self):
        code, out = run_close(["lint", "--wiki", str(self.repo.wiki)], self.FOOTER)
        self.assertEqual(code, 0, out)
        row = rows(self.repo.wiki, "lint_log.jsonl")[-1]
        self.assertEqual((row["lint_type"], row["verdict"], row["total_pages"]), ("full", "issues", 40))
        self.assertIn("] lint ", (self.repo.wiki / "log.md").read_text(encoding="utf-8"))
        self.assertEqual(git(self.repo.root, "log", "-1", "--format=%s"),
                         "lint: 2 flags, no fixes applied at ingest #12 — 40 pages")

    def test_strict_footer(self):
        bad = self.FOOTER.replace("Total pages: 40\n", "")
        code, out = run_close(["lint", "--wiki", str(self.repo.wiki)], bad)
        self.assertEqual(code, 2)
        self.assertIn("Total pages", out["error"])
        self.assertEqual(rows(self.repo.wiki, "lint_log.jsonl"), [])
        self.assertNotIn("] lint ", (self.repo.wiki / "log.md").read_text(encoding="utf-8"))


class TestCloseOverviewQueryAsk(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_overview_counts_ingests(self):
        code, out = run_close(["overview", "--wiki", str(self.repo.wiki), "--mode", "soft", "--status",
                               "no_changes"], "<usage><duration_ms>5</duration_ms></usage>")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["ingest_count"], 1)
        self.assertEqual(rows(self.repo.wiki, "overview_log.jsonl")[-1]["status"], "no_changes")
        self.assertEqual(git(self.repo.root, "log", "-1", "--format=%s"),
                         "overview: telemetry for soft refresh at ingest #1")

    def test_query_footer_parse(self):
        text = ("Answer.\n\nPages consulted: [[agents]], [[pages/concepts/rag.md]], [[agents|Agents]]\n"
                "Filed as: pages/syntheses/my-answer.md\n<usage><subagent_tokens>3</subagent_tokens>"
                "<duration_ms>7</duration_ms></usage>")
        code, out = run_close(["query", "--wiki", str(self.repo.wiki), "--query", "what?",
                               "--search-mode", "hybrid", "--top-score", "0.8"], text)
        self.assertEqual(code, 0, out)
        row = rows(self.repo.wiki, "query_log.jsonl")[-1]
        self.assertEqual(row["pages_consulted"], ["agents", "rag"])
        self.assertEqual(row["filed_as"], "my-answer")
        self.assertEqual(git(self.repo.root, "log", "-1", "--format=%s"), "query: telemetry")

    def test_summarize_not_filed_and_ask(self):
        code, out = run_close(["summarize", "--wiki", str(self.repo.wiki), "--query", "topic",
                               "--search-mode", "hybrid", "--routed-from", "ask"],
                              "Pages consulted: [[a]]\nFiled as: Not filed\n<usage><duration_ms>1</duration_ms></usage>")
        self.assertEqual(code, 0, out)
        row = rows(self.repo.wiki, "query_log.jsonl")[-1]
        self.assertEqual((row["query_skill"], row["filed_as"], row["routed_from"]), ("summarize", None, "ask"))
        code, out = run_close(["ask", "--wiki", str(self.repo.wiki), "--query", "topic", "--routed-to",
                               "summarize", "--duration-ms", "3"])
        self.assertEqual(code, 0, out)
        self.assertEqual(rows(self.repo.wiki, "query_log.jsonl")[-1]["routed_to"], "summarize")
        self.assertEqual(git(self.repo.root, "status", "--porcelain"), "")

    def test_graph_row_committed(self):
        code, out = run_close(["graph", "--wiki", str(self.repo.wiki), "--query", "neighbors agents --depth 1",
                               "--pages-consulted", "agents,rag", "--duration-ms", "12"])
        self.assertEqual(code, 0, out)
        row = rows(self.repo.wiki, "query_log.jsonl")[-1]
        self.assertEqual((row["query_skill"], row["search_mode"], row["pages_consulted"]),
                         ("graph", None, ["agents", "rag"]))
        self.assertEqual(git(self.repo.root, "log", "-1", "--format=%s"), "graph: telemetry")


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self._tmp.name))
        self.out = self.repo.root / "tmpout"

    def tearDown(self):
        self._tmp.cleanup()

    def pf(self, source: Path):
        return ingest_preflight.run(self.repo.wiki, source, self.out)

    def test_protocol_file_refused_not_renamed(self):
        proto = self.repo.root / "vault/.wiki-identity-dev.md"
        proto.write_text("---\nowner: wiki\n---\nx\n", encoding="utf-8")
        code, rep = self.pf(proto)
        self.assertEqual(code, 2)
        self.assertIn("excluded", rep["error"])
        self.assertTrue(proto.exists())
        nas = self.repo.root / "vault/Clippings/2026-11-04-notes.md"
        nas.write_text("---\nnot_a_source: true\n---\nx\n", encoding="utf-8")
        self.assertEqual(self.pf(nas)[0], 2)

    def test_rename_untracked_and_collision(self):
        clip = self.repo.root / "vault/Clippings/2026-11-05-Some Title.md"
        clip.write_text("---\ntitle: Some Title\n---\n" + "w " * 2000, encoding="utf-8")
        code, rep = self.pf(clip)
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["source"], "vault/Clippings/2026-11-05-some-title.md")
        self.assertIn("renamed", rep["gates"])
        self.assertEqual(rep["model"], "opus")
        self.assertTrue((self.repo.root / rep["brief_path"]).is_file() or Path(rep["brief_path"]).is_file())
        clash = self.repo.root / "vault/Clippings/2026-11-05-Some_Title.md"
        clash.write_text("---\ntitle: Some Title\n---\nx\n", encoding="utf-8")
        self.assertEqual(self.pf(clash)[0], 1)
        self.assertTrue(clash.exists())

    def test_duplicate_listicle_and_triage(self):
        (self.repo.root / "dev/ingest-triage-2026-11-01.md").write_text(TRIAGE, encoding="utf-8")
        src = self.repo.root / "vault/Clippings/2026-11-02-new-source.md"
        src.write_text("---\ntitle: New Source\nquality: listicle\n---\n" + "word " * 3000, encoding="utf-8")
        import hashlib
        sha = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
        (self.repo.wiki / "pages/sources/2026-10-01-other.md").write_text(f"---\nsource_hash: {sha}\n---\n",
                                                                          encoding="utf-8")
        code, rep = self.pf(src)
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["duplicates"], ["pages/sources/2026-10-01-other.md"])
        self.assertIn("duplicate", rep["gates"])
        self.assertEqual(rep["model"], "sonnet")
        self.assertEqual(rep["triage"]["batch"], 1)


class TestUsageTranscriptDuration(unittest.TestCase):
    def test_span_fills_missing_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = str(Path(tmp) / "proj")
            d = Path(tmp) / "projects" / parse_agent_usage.project_slug(cwd) / "sess" / "subagents"
            d.mkdir(parents=True)
            recs = [
                {"type": "user", "promptId": "p1", "timestamp": "2026-11-15T10:00:00.000Z"},
                {"type": "assistant", "promptId": "p1", "timestamp": "2026-11-15T10:00:30.500Z",
                 "message": {"id": "m1", "model": "x", "usage": {"input_tokens": 5}}},
                # Resumed turn an hour later: the idle gap must not count.
                {"type": "user", "promptId": "p2", "timestamp": "2026-11-15T11:00:00.000Z"},
                {"type": "assistant", "promptId": "p2", "timestamp": "2026-11-15T11:00:10.000Z",
                 "message": {"id": "m2", "model": "x", "usage": {"input_tokens": 1}}},
            ]
            (d / "agent-abc.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
            res = parse_agent_usage.parse_usage_text("")
            parse_agent_usage.enrich(res, ["abc"], cwd, str(Path(tmp) / "projects"))
            self.assertEqual(res["duration_ms"], 40500)
            self.assertEqual(res["duration_source"], "transcript")
            self.assertEqual(res["input_tokens"], 6)
            # A <usage> duration always wins.
            res2 = parse_agent_usage.parse_usage_text("<usage><duration_ms>7</duration_ms></usage>")
            parse_agent_usage.enrich(res2, ["abc"], cwd, str(Path(tmp) / "projects"))
            self.assertEqual(res2["duration_ms"], 7)
            self.assertNotIn("duration_source", res2)


if __name__ == "__main__":
    unittest.main()
