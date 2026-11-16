"""Guard: subagent-visible bash blocks hold only plain, single commands (s140).

In auto mode, compound Bash commands (`cd`, `&&`/`||`/`;` chains, pipes,
`tee`, heredocs, loops, `2>&1`) fall outside allow-rule matching and go to
the classifier, which denied a linter's `cd … && update_index.py … | tail;
git status` and a heredoc edit script mid-fix in s139. The playbooks now tell
agents to run one plain command per Bash call; this test keeps the commands
they quote in that shape, so a model copying an example never copies a
compound one.

Scope: the four agent playbooks plus the reference files those agents Read
by dispatch path. Main-session skills are out of scope (their heredocs feed
closers on stdin by design).

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

here = Path(__file__).resolve()


def _plugin_root() -> Path:
    for cand in (here.parents[2] / "wiki-forge", here.parents[1]):
        if (cand / "agents").is_dir() and (cand / "scripts").is_dir():
            return cand
    raise RuntimeError(f"cannot locate the wiki-forge plugin root from {here}")


ROOT = _plugin_root()

AGENT_VISIBLE = sorted(
    list((ROOT / "agents").glob("*.md"))
    + list((ROOT / "skills" / "wiki-ingest" / "references").glob("ingestor-*.md"))
    + [
        ROOT / "skills" / "wiki-overview" / "references" / "curator-mode-b.md",
        ROOT / "skills" / "wiki-summarize" / "references" / "librarian-survey.md",
    ]
)

FENCE = re.compile(r"^\s*```(\w*)\s*$")
QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')
PLACEHOLDER = re.compile(r"<[^<>\s]*>")  # `<Y|P|N>`, `<slug>`: doc placeholders, not shell
FORBIDDEN = [
    (re.compile(r"(^|\s)cd\s"), "cd"),
    (re.compile(r"&&"), "&&"),
    (re.compile(r"\|\|"), "||"),
    (re.compile(r"\|"), "pipe"),
    (re.compile(r";"), "; chain"),
    (re.compile(r"<<"), "heredoc"),
    (re.compile(r"(^|\s)tee(\s|$)"), "tee"),
    (re.compile(r"2>&1|>/dev/null"), "redirect"),
    (re.compile(r"^\s*(for|while|if)\s"), "shell control flow"),
    (re.compile(r"python3?\s+-c\b"), "python -c"),
]


def bash_commands(text: str):
    """Yield (line_no, command) for each logical command in ```bash blocks."""
    in_bash = False
    buf, start = "", 0
    for i, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            if in_bash:
                in_bash = False
            elif m.group(1) in ("bash", "sh", "shell"):
                in_bash = True
            continue
        if not in_bash:
            continue
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if not buf:
            start = i
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        yield start, buf + stripped
        buf = ""


def violations(text: str):
    out = []
    for line_no, cmd in bash_commands(text):
        bare = PLACEHOLDER.sub("X", QUOTED.sub('""', cmd))
        for rx, label in FORBIDDEN:
            if rx.search(bare):
                out.append((line_no, label, cmd))
    return out


class TestAgentCommandShapes(unittest.TestCase):
    def test_scope_files_exist(self):
        for p in AGENT_VISIBLE:
            self.assertTrue(p.is_file(), f"missing agent-visible file: {p}")
        self.assertEqual(len(list((ROOT / "agents").glob("*.md"))), 4)

    def test_no_compound_commands(self):
        found = []
        for p in AGENT_VISIBLE:
            for line_no, label, cmd in violations(p.read_text(encoding="utf-8")):
                found.append(f"{p.relative_to(ROOT)}:{line_no} [{label}] {cmd}")
        self.assertEqual(found, [], "compound command shapes:\n" + "\n".join(found))

    def test_every_agent_states_the_rule(self):
        for p in (ROOT / "agents").glob("*.md"):
            text = p.read_text(encoding="utf-8")
            self.assertIn("One plain command per Bash call", text, p.name)
            self.assertIn("On a permission or classifier denial, stop", text, p.name)

    def test_detector_catches_known_bad_shapes(self):
        bad = (
            "```bash\n"
            "cd C:/x && python u.py wiki 2>&1 | tail -3; git status\n"
            "```\n"
            "```bash\n"
            "mkdir -p t && python g.py w --json | tee t/g.json\n"
            "```\n"
            "```bash\n"
            "python - <<'PY'\n"
            "```\n"
        )
        labels = {label for _, label, _ in violations(bad)}
        for want in ("cd", "&&", "pipe", "; chain", "redirect", "tee", "heredoc"):
            self.assertIn(want, labels)

    def test_detector_ignores_quoted_text_and_continuations(self):
        ok = (
            "```bash\n"
            "git -C w commit -m \"lint: a | b; c && d\"\n"
            "python \"${P}/scripts/append_log.py\" ingest \\\n"
            "  --title \"x\" \\\n"
            "  --wiki w\n"
            "```\n"
        )
        self.assertEqual(violations(ok), [])


class TestGraphAnalyzerOut(unittest.TestCase):
    """`graph_analyzer.py --out` replaces the linter's `mkdir -p && … | tee`."""

    def test_out_writes_stash_and_prints_same_json(self):
        import json
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            wiki = Path(td) / "wiki"
            (wiki / "pages" / "concepts").mkdir(parents=True)
            (wiki / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
            for a, b in (("alpha", "beta"), ("beta", "alpha")):
                (wiki / "pages" / "concepts" / f"{a}.md").write_text(
                    f"---\ntitle: {a}\n---\n\nSee [[{b}]].\n", encoding="utf-8"
                )
            stash = Path(td) / "nested" / "tmp" / "wiki-graph.json"
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "graph_analyzer.py"),
                 str(wiki), "--out", str(stash)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(stash.is_file())
            self.assertEqual(json.loads(proc.stdout), json.loads(stash.read_text(encoding="utf-8")))
            self.assertEqual(json.loads(proc.stdout)["total_pages"], 2)


if __name__ == "__main__":
    unittest.main()
