"""Unit tests for `session_report.py` (SessionStart drill + pre-rendered report)
and `tech_debt_stats.py --dev-report` (s141, script audit #5).

Both scripts are project-local (`.claude/hooks/`, `tools/`), not part of the
wiki-forge plugin tree. In a standalone wiki-forge checkout those directories
are absent, so the affected classes SKIP, keeping this file byte-identical
across the dev/tests <-> wiki-forge/tests mirror.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # live repo: dev/tests -> repo root
_HOOKS = _ROOT / ".claude" / "hooks"
_TOOLS = _ROOT / "tools"

sr = tds = None
if (_HOOKS / "session_report.py").is_file():
    sys.path.insert(0, str(_HOOKS))
    import session_report as sr  # noqa: E402
if (_TOOLS / "tech_debt_stats.py").is_file():
    sys.path.insert(0, str(_TOOLS))
    import tech_debt_stats as tds  # noqa: E402


def _drill(**over):
    d = {
        "graph_line": "hubs a (3) | clean",
        "lint_counter": 4, "lint_overdue": False,
        "overview": {"stale_by": 2, "should_rewrite": False, "trigger_reasons": []},
        "backlog": {"pending": 0, "state": "all_clear", "triage_created": None,
                    "batch_label": None, "batch_first_slug": None, "stale_count": 0},
        "scale_audit": {"should_audit": False, "fired_count": 0, "reasons": []},
    }
    d.update(over)
    return d


def _data(**over):
    data = {"swept": None, "git": {"modified": 0, "untracked": 0, "all_obsidian": False},
            "memory": 3, "drill": _drill(), "hub": {"reachable": False},
            "lease": {"present": False}, "experiment": None}
    data.update(over)
    return data


@unittest.skipIf(sr is None, ".claude/hooks/ absent (standalone wiki-forge checkout)")
class TestRender(unittest.TestCase):
    def test_quiet_report(self):
        out = sr.render(_data())
        self.assertIn("== Wiki bootstrapped — OP MODE ==", out)
        self.assertIn("- Hub: offline (hub not running)", out)
        self.assertIn("- Git: clean", out)
        self.assertIn("- Memory: 3 entries loaded", out)
        self.assertIn("- Lint: 4/10 since last — fresh", out)
        self.assertIn("- Graph: hubs a (3) | clean", out)
        self.assertIn("- Overview: fresh", out)
        self.assertIn("- Backlog: all clear", out)
        self.assertNotIn("Offer:", out)
        self.assertNotIn("⚠", out)

    def test_memory_singular_and_fresh_machine(self):
        self.assertIn("- Memory: 1 entry loaded", sr.render(_data(memory=1)))
        self.assertIn("- Memory: empty (fresh machine)", sr.render(_data(memory=None)))

    def test_health_offers_outrank_backlog_and_cap_two(self):
        drill = _drill(lint_counter=13, lint_overdue=True,
                       overview={"stale_by": 12, "should_rewrite": True, "trigger_reasons": ["hub drift"]},
                       backlog={"pending": 5, "state": "untriaged", "stale_count": 0})
        out = sr.render(_data(drill=drill))
        self.assertTrue(out.startswith("⚠ LINT OVERDUE — 13 ingests since last full lint (cadence: every 10)"))
        self.assertIn("- Lint: OVERDUE by 3", out)
        self.assertIn("- Overview: 12 ingests stale since soft-update; structural triggers: hub drift", out)
        offers = [l for l in out.splitlines() if l.startswith("Offer:")]
        self.assertEqual(len(offers), 2)
        self.assertIn("/wiki-lint", offers[0])
        self.assertIn("/wiki-overview --soft", offers[1])
        self.assertNotIn("backlog offer line", out)  # backlog offer was capped out

    def test_backlog_states_and_dev_tag(self):
        tri = _drill(backlog={"pending": 7, "state": "triaged", "batch_label": "Batch 4",
                              "batch_first_slug": "2026-01-01-x", "stale_count": 0})
        out = sr.render(_data(drill=tri))
        self.assertIn("- Backlog: 7 pending, triaged — Batch 4 next", out)
        self.assertIn("Offer: pick up Batch 4 with /wiki-ingest? First up: 2026-01-01-x.", out)
        self.assertIn("<!-- backlog offer line (drop in dev mode): Offer: pick up Batch 4", out)
        stale = _drill(backlog={"pending": 9, "state": "stale", "stale_count": 2, "triage_created": "2026-11-01"})
        self.assertIn("- Backlog: 9 pending, triage STALE — 2 new since 2026-11-01", sr.render(_data(drill=stale)))

    def test_verify_field_and_offer(self):
        vq = {"total": 119, "aged": 113, "older_than_days": 60, "oldest_days": 202, "oldest_slug": "x"}
        out = sr.render(_data(drill=_drill(verify_queue=vq)))
        self.assertIn("- Backlog: all clear\n- Verify: 119 flagged — 113 untouched ≥60d (oldest 202d)\n", out)
        self.assertIn("Offer: drain the oldest 10 flags with /wiki-corroborate --batch 1-10? (113 untouched ≥60d)", out)
        self.assertNotIn("backlog offer line", out)  # health offer, survives dev mode

    def test_verify_no_aged_no_offer_and_empty_queue(self):
        young = {"total": 5, "aged": 0, "older_than_days": 60, "oldest_days": 12}
        out = sr.render(_data(drill=_drill(verify_queue=young)))
        self.assertIn("- Verify: 5 flagged — 0 untouched ≥60d (oldest 12d)", out)
        self.assertNotIn("Offer:", out)
        out = sr.render(_data(drill=_drill(verify_queue={"total": 0, "aged": 0, "oldest_days": None})))
        self.assertIn("- Verify: queue empty", out)
        self.assertNotIn("- Verify:", sr.render(_data()))  # older drill JSON without the field

    def test_feed_field_and_offer_outside_cap(self):
        feed = {"files": ["vault-missing-2026-11-17.html"], "remaining": 602, "pending": False}
        drill = _drill(lint_counter=13, lint_overdue=True,
                       overview={"stale_by": 12, "should_rewrite": False, "trigger_reasons": []})
        out = sr.render(_data(drill=drill, feed=feed))
        self.assertIn("- Feed: 602 Medium articles to clip (vault-missing-2026-11-17.html)", out)
        offers = [l for l in out.splitlines() if l.startswith("Offer:")]
        self.assertEqual(len(offers), 3)  # two health offers + the feed offer, never capped out
        self.assertIn("/medium-feed? (602 remaining)", offers[2])
        self.assertIn("<!-- backlog offer line (drop in dev mode): Offer: open the next 10", out)
        self.assertNotIn("- Feed:", sr.render(_data()))

    def test_feed_pending_batch_and_multiple_files(self):
        feed = {"files": ["vault-missing-a.html", "vault-missing-b.html"], "remaining": 7, "pending": True}
        out = sr.render(_data(feed=feed))
        self.assertIn("- Feed: 7 Medium articles to clip (vault-missing-a.html +1 more); "
                      "a batch is open, not yet reconciled", out)
        self.assertIn("Offer: check the open clip batch with /medium-feed?", out)

    def test_medium_feed_gather(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertIsNone(sr.medium_feed(root))
            (root / "medium-incoming").mkdir()
            (root / "medium-incoming" / "vault-missing-2026-11-17.html").write_text(
                '<DT><A HREF="https://medium.com/p/aaaaaaaaaaaa">A</A>\n'
                '<DT><A HREF="https://medium.com/p/bbbbbbbbbbbb">B</A>\n', encoding="utf-8")
            got = sr.medium_feed(root)
            self.assertEqual(got, {"files": ["vault-missing-2026-11-17.html"], "remaining": 2, "pending": False})

    def test_verify_offer_ranks_after_lint_and_overview(self):
        drill = _drill(lint_counter=11, lint_overdue=True,
                       overview={"stale_by": 12, "should_rewrite": False, "trigger_reasons": []},
                       verify_queue={"total": 9, "aged": 9, "older_than_days": 60, "oldest_days": 90})
        offers = [l for l in sr.render(_data(drill=drill)).splitlines() if l.startswith("Offer:")]
        self.assertEqual(len(offers), 2)
        self.assertNotIn("/wiki-corroborate", "".join(offers))
        drill["lint_overdue"] = False
        offers = [l for l in sr.render(_data(drill=drill)).splitlines() if l.startswith("Offer:")]
        self.assertIn("/wiki-corroborate", offers[1])

    def test_hub_and_lease(self):
        hub = {"reachable": True, "registered": True, "status": "offline", "last_seen_age_s": 8}
        out = sr.render(_data(hub=hub))
        self.assertIn("- Hub: reachable — wiki-forge offline (last seen 8s ago); enable agent-hub", out)
        out = sr.render(_data(hub=hub, lease={"present": True, "fresh": True, "age_s": 42.7}))
        self.assertIn("SERVING elsewhere (lease age 42s)", out)
        self.assertNotIn("enable agent-hub", out)
        out = sr.render(_data(hub=hub, lease={"present": True, "fresh": False, "age_s": 9999}))
        self.assertIn("enable agent-hub", out)

    def test_git_field(self):
        g = {"modified": 4, "untracked": 0, "all_obsidian": True}
        self.assertIn("- Git: 4 modified, 0 untracked (all Obsidian UI drift); committed clip arrivals (abc1234)",
                      sr.render(_data(git=g, swept="abc1234")))

    def test_banners(self):
        drill = _drill(scale_audit={"should_audit": True, "fired_count": 2, "reasons": ["pages", "ratio"]})
        exp = {"title": "Inline vs fork", "firing_trigger": "Batch 13", "protocol": "dev/x.md"}
        out = sr.render(_data(drill=drill, experiment=exp))
        self.assertIn("⚙ EXPERIMENT READY — Inline vs fork (firing_trigger: Batch 13; protocol: dev/x.md)", out)
        self.assertIn("⚙ SCALE AUDIT DUE — 2/5 Mode-7 triggers fired (pages, ratio)", out)
        self.assertIn("Offer: run /wiki-scale-audit", out)

    def test_empty_drill_raises(self):
        with self.assertRaises(RuntimeError):
            sr.render(_data(drill={}))

    def test_experiment_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(sr.experiment(root))
            (root / "dev").mkdir()
            (root / "dev" / ".experiment-active.md").write_text(
                "---\ntitle: \"Probe\"\nfiring_trigger: next ingest\n---\nbody\n", encoding="utf-8")
            self.assertEqual(sr.experiment(root), {"title": "Probe", "firing_trigger": "next ingest", "protocol": None})

    def test_memory_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, home = Path(tmp) / "proj", Path(tmp) / "home"
            root.mkdir()
            self.assertIsNone(sr.memory_count(root, home))
            mem = home / ".claude" / "projects" / sr.project_slug(root) / "memory"
            mem.mkdir(parents=True)
            (mem / "MEMORY.md").write_text("- [a](a.md) — x\n- [b](b.md) — y\nnot an entry\n", encoding="utf-8")
            self.assertEqual(sr.memory_count(root, home), 2)

    def test_memory_dir_setting_and_unwired_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, home = Path(tmp) / "proj", Path(tmp) / "home"
            (root / ".claude").mkdir(parents=True)
            self.assertFalse(sr.memory_unwired(root, home))  # no repo memories/
            repo = root / "memories"
            repo.mkdir()
            (repo / "MEMORY.md").write_text("- [a](a.md) — x\n", encoding="utf-8")
            self.assertTrue(sr.memory_unwired(root, home))  # default dir, repo copy ignored
            self.assertIsNone(sr.memory_count(root, home))
            (root / ".claude" / "settings.local.json").write_text(
                json.dumps({"autoMemoryDirectory": str(repo)}), encoding="utf-8")
            self.assertEqual(sr.memory_dir(root, home), repo)
            self.assertFalse(sr.memory_unwired(root, home))
            self.assertEqual(sr.memory_count(root, home), 1)
            (root / ".claude" / "settings.local.json").write_text(
                json.dumps({"autoMemoryDirectory": "~/mem"}), encoding="utf-8")
            self.assertEqual(sr.memory_dir(root, home), home / "mem")

    def test_memory_unwired_render(self):
        out = sr.render(_data(memory=2, memory_unwired=True))
        self.assertIn("- Memory: 2 entries loaded; ⚠ repo memories/ not wired on this PC", out)
        self.assertNotIn("not wired", sr.render(_data(memory=2)))

    def test_main_fails_open(self):
        import io
        import contextlib
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vault").mkdir()
            (root / "vault" / ".wiki-identity-bootstrap.md").write_text("# Boot\n", encoding="utf-8")
            old_env, old_gather = os.environ.get("CLAUDE_PROJECT_DIR"), sr.gather
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            sr.gather = lambda r: (_ for _ in ()).throw(OSError("boom"))
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(sr.main(["session_report.py"]), 0)
            finally:
                sr.gather = old_gather
                if old_env is None:
                    os.environ.pop("CLAUDE_PROJECT_DIR", None)
                else:
                    os.environ["CLAUDE_PROJECT_DIR"] = old_env
            ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
            self.assertTrue(ctx.startswith("# Boot"))
            self.assertIn("## Pre-rendered report", ctx)
            self.assertIn("UNAVAILABLE", ctx)
            self.assertFalse((root / ".claude" / ".session-mode").exists())


_TD = """# Tech debt

## Open
_To-do first. **Cross-references use short names in brackets, never list positions** (entries are deleted when they ship, so positions drift): [Alpha], [Beta]; Gated: [Gee]._

> **PINNED: verify s99 live first.**

- **Alpha thing `code`** *(filed s1)*
  **Depends on:** none · **Feeds:** [Beta] (faster)
  **Problem:** x
- **Beta thing**
  **Depends on:** [Alpha] · **Feeds:** publishing

## Gated
- **Gee thing**
  **Accelerated by:** [Alpha]
  **Trigger last-checked:** 2026-11-05 — 1/3 fired.

## Frozen
- **Frozen thing**

## Decisions / guards
- **Not debt**
"""


@unittest.skipIf(tds is None, "tools/ absent (standalone wiki-forge checkout)")
class TestDevReport(unittest.TestCase):
    def test_parse_and_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dev").mkdir()
            (root / "dev" / "sessions.md").write_text(
                "---\nx: 1\n---\n# H\n\n## 2026-11-16 (session 141 — thing)\n\n## 2026-11-15 (session 140 — old)\n",
                encoding="utf-8")
            out = tds.dev_report(_TD, root)
        self.assertIn("- Tech-debt queue: 2 Open, 1 Gated, 1 Frozen", out)
        self.assertIn("- Last session: 2026-11-16 — session 141 — thing", out)
        self.assertIn("| 0 | PINNED: verify s99 live first. | — | — |", out)
        self.assertIn("| 1 | [Alpha] Alpha thing code | none | [Beta] (faster) |", out)
        self.assertIn("| 2 | [Beta] Beta thing | [Alpha] | publishing |", out)
        self.assertIn("| [Gee] Gee thing | 2026-11-05 — 1/3 fired. | [Alpha] |", out)
        self.assertNotIn("Frozen thing", out)
        self.assertNotIn("Not debt", out)

    def test_legend_mismatch_falls_back_to_title(self):
        td = _TD.replace("[Alpha], [Beta]; Gated", "[Alpha]; Gated")
        out = tds.dev_report(td, Path(tempfile.gettempdir()))
        self.assertIn("| 1 | Alpha thing code |", out)


@unittest.skipIf(sr is None, ".claude/hooks/ absent (standalone wiki-forge checkout)")
class TestClipSweep(unittest.TestCase):
    """clip_sweep normalizes raw-title clip filenames on arrival, then commits
    explicit paths only (s147: Obsidian Chrome extension clips)."""

    def setUp(self):
        import shutil
        import subprocess
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        scripts = self.root / "wiki-forge" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("normalize_vault.py", "frontmatter.py"):
            shutil.copy(_ROOT / "wiki-forge" / "scripts" / name, scripts / name)
        (self.root / "vault" / "Clippings").mkdir(parents=True)
        self.git = lambda *a: subprocess.run(["git", *a], cwd=self.root, capture_output=True,
                                             text=True, encoding="utf-8")
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("add", "wiki-forge")
        self.git("commit", "-q", "-m", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def _clip(self, name, title):
        (self.root / "vault" / "Clippings" / name).write_text(
            f'---\ntitle: "{title}"\n---\nbody\n', encoding="utf-8")

    def test_untracked_raw_title_is_normalized_and_committed(self):
        self._clip("2026-10-22-My Second Brain’s Cockpit (Now).md", "My Second Brain’s Cockpit (Now)")
        self._clip("2026-10-23-already-canonical.md", "Already Canonical")
        (self.root / "notes.md").write_text("unrelated\n", encoding="utf-8")
        res = sr.clip_sweep(self.root)
        self.assertTrue(res["hash"])
        self.assertEqual(len(res["renamed"]), 1)
        tracked = self.git("ls-files", "vault").stdout.split()
        self.assertEqual(sorted(tracked), ["vault/Clippings/2026-10-22-my-second-brains-cockpit-now.md",
                                           "vault/Clippings/2026-10-23-already-canonical.md"])
        self.assertIn("?? notes.md", self.git("status", "--porcelain").stdout)
        self.assertIn("normalized 1 filename", self.git("log", "-1", "--format=%s").stdout)

    def test_tracked_raw_title_is_git_mv_renamed(self):
        self._clip("2026-10-22-Raw Title Here.md", "Raw Title Here")
        self.git("add", "vault")
        self.git("commit", "-q", "-m", "raw clip")
        res = sr.clip_sweep(self.root)
        self.assertTrue(res["hash"])
        self.assertEqual(self.git("ls-files", "vault").stdout.split(),
                         ["vault/Clippings/2026-10-22-raw-title-here.md"])
        self.assertEqual(self.git("status", "--porcelain", "--", "vault").stdout.strip(), "")

    def test_nothing_to_do(self):
        self.assertEqual(sr.clip_sweep(self.root), {"hash": None, "renamed": []})

    def test_git_field_mentions_normalized(self):
        g = {"modified": 0, "untracked": 0, "all_obsidian": False}
        self.assertEqual(sr.git_field(g, "abc1234", 3),
                         "clean; committed clip arrivals (abc1234), normalized 3 filenames")


if __name__ == "__main__":
    unittest.main()
