#!/usr/bin/env python3
"""Tests for install_deps.py — the plugin's idempotent, fail-soft semantic-search
deps provisioner (wiki-forge Phase-1 Step 5, 2026-06-15).

Two contracts are pinned:

  * The pure `needs_install` manifest-diff guard. It decides whether pip runs,
    so it must say "skip" only when the target dir exists AND the cached manifest
    byte-matches the bundled one — otherwise the SessionStart hook either
    re-installs every session (cost regression) or fails to pick up a
    requirements bump (stale deps).

  * The fail-soft invariant: main() must NEVER return non-zero. Semantic search
    is OPTIONAL (degrades to BM25), so a blocking exit code from this hook would
    freeze session start over a non-essential feature. These tests exercise the
    no-pip paths (bad args, absent requirements.txt) and assert exit 0.

No test invokes pip or the network — only the pure guard and the early-return
branches are exercised.

Run:  python run_tests.py   (from the tests dir; or run this file directly)

Exit codes: 0 = all tests pass; 1 = at least one test failed.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# Locate the canonical helper-scripts dir in either the live repo (dev/tests/ ->
# wiki-forge/scripts) or a standalone wiki-forge checkout (tests/ -> scripts), so
# this file stays byte-identical across both repos.
def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (
        here.parents[2] / "wiki-forge" / "scripts",
        here.parents[1] / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))

import install_deps  # noqa: E402


class TestNeedsInstall(unittest.TestCase):
    """The manifest-diff idempotency guard — pure, no pip."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test_install_deps_"))
        self.req = self.tmp / "requirements.txt"
        self.req.write_text("numpy==2.4.4\n", encoding="utf-8")
        self.cached = self.tmp / "cached.txt"
        self.target = self.tmp / "pylib"

    def test_target_missing_needs_install(self):
        """Manifests match but the pylib dir is gone → must reinstall."""
        self.cached.write_text("numpy==2.4.4\n", encoding="utf-8")
        self.assertTrue(install_deps.needs_install(self.req, self.cached, self.target))

    def test_no_cached_manifest_needs_install(self):
        """First run: target exists but nothing stamped yet → install."""
        self.target.mkdir()
        self.assertTrue(install_deps.needs_install(self.req, self.cached, self.target))

    def test_manifest_match_skips(self):
        """Steady state: target present + cached manifest matches → skip."""
        self.target.mkdir()
        self.cached.write_text("numpy==2.4.4\n", encoding="utf-8")
        self.assertFalse(install_deps.needs_install(self.req, self.cached, self.target))

    def test_manifest_drift_needs_install(self):
        """Requirements bumped since last install → reinstall."""
        self.target.mkdir()
        self.cached.write_text("numpy==2.0.0\n", encoding="utf-8")
        self.assertTrue(install_deps.needs_install(self.req, self.cached, self.target))


class TestFailSoft(unittest.TestCase):
    """main() must never return non-zero — an optional feature must not block startup."""

    def test_too_few_args_returns_zero(self):
        with patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(install_deps.main(["install_deps.py"]), 0)

    def test_missing_requirements_returns_zero(self):
        """plugin_root with no requirements.txt → skip, no pip, exit 0."""
        tmp = Path(tempfile.mkdtemp(prefix="test_install_deps_"))
        with patch("sys.stderr", new=io.StringIO()):
            rc = install_deps.main(["install_deps.py", str(tmp / "noroot"), str(tmp / "data")])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
