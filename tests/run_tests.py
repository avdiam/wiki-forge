#!/usr/bin/env python3
"""Discovery runner for the wiki-forge helper-script test suite.

Stdlib `unittest` only — no pytest, no third-party deps. Discovers every
`test_*.py` in this directory, puts the helper-scripts dir on `sys.path` so
the tests can import the modules under test, and runs them.

The suite tests the helper scripts that live in `wiki-forge/scripts/` (live repo)
or `skills/wiki-init/templates/scripts/` (wiki-forge mirror). The path probe
below resolves whichever layout this copy sits in, so this file — like the
test files it discovers — stays byte-identical across both repos.

Run:
    python dev/tests/run_tests.py        # from the live repo root
    python run_tests.py                  # from inside the tests dir

Exit codes: 0 = all tests passed; 1 = at least one test failed or errored.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _scripts_dir() -> Path:
    """Locate the helper-scripts dir in either the live or wiki-forge layout."""
    for cand in (
        TESTS_DIR.parents[1] / "wiki-forge" / "scripts",
        TESTS_DIR.parent / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {TESTS_DIR}")


def main() -> int:
    sys.path.insert(0, str(_scripts_dir()))
    sys.path.insert(0, str(TESTS_DIR))
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
