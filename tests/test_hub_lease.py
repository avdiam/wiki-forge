"""Unit tests for `hub_lease.py` — the agent-hub serve/live sentinel lease manager.

Pins the session-126 collision-ergonomics contract:
- `clean` deletes ONLY a stale sentinel (crash cleanup); a fresh lease from a
  live loop in another session survives a new session start.
- `arm` refuses while a fresh foreign lease is held; replaces a stale one;
  writes an owner nonce.
- `beat` refreshes mtime when the lease is ours (heartbeat), reports
  operator-stop (2) when the file is gone, and takeover (3) on nonce mismatch.
- `stop` is the idempotent operator kill switch.
- `status` reports present/age/fresh/nonce without mutating anything.

The script lives at `.claude/hooks/hub_lease.py` (project-local, not part of
the wiki-forge plugin tree). In a standalone wiki-forge checkout that
directory is absent, so this whole module SKIPS — keeping the file
byte-identical across the dev/tests ↔ wiki-forge/tests mirror.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _hooks_dir() -> Path | None:
    here = Path(__file__).resolve()
    cand = here.parents[2] / ".claude" / "hooks"  # live repo: dev/tests -> repo root
    return cand if cand.is_dir() else None


_HOOKS = _hooks_dir()
if _HOOKS is not None:
    sys.path.insert(0, str(_HOOKS))
    import hub_lease as hl  # noqa: E402
else:
    hl = None

TTL = 30.0  # minutes, the default


@unittest.skipIf(hl is None, ".claude/hooks/ absent (standalone wiki-forge checkout)")
class TestHubLease(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / ".agent-hub-live.active")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, nonce: str = "owner-abc", age_s: float = 0.0) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(nonce)
        if age_s:
            past = time.time() - age_s
            os.utime(self.path, (past, past))

    # --- clean ---

    def test_clean_absent_is_noop(self) -> None:
        self.assertEqual(hl.clean(self.path, TTL), "absent")

    def test_clean_fresh_lease_survives(self) -> None:
        self._make(age_s=60)  # 1 min old — a live loop's heartbeat
        self.assertEqual(hl.clean(self.path, TTL), "fresh")
        self.assertTrue(os.path.exists(self.path))

    def test_clean_stale_lease_cleared(self) -> None:
        self._make(age_s=TTL * 60 + 120)  # past TTL — crashed session
        self.assertEqual(hl.clean(self.path, TTL), "cleared")
        self.assertFalse(os.path.exists(self.path))

    def test_clean_boundary_exactly_ttl_is_fresh(self) -> None:
        now = time.time()
        self._make(age_s=0)
        os.utime(self.path, (now - TTL * 60, now - TTL * 60))
        self.assertEqual(hl.clean(self.path, TTL, now=now), "fresh")

    # --- arm ---

    def test_arm_on_empty_acquires_and_writes_nonce(self) -> None:
        ok, nonce = hl.arm(self.path, TTL)
        self.assertTrue(ok)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), nonce)

    def test_arm_refused_while_fresh_foreign_lease_held(self) -> None:
        self._make(nonce="other-session", age_s=60)
        ok, reason = hl.arm(self.path, TTL)
        self.assertFalse(ok)
        self.assertIn("active", reason)
        # the foreign lease is untouched
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "other-session")

    def test_arm_replaces_stale_lease(self) -> None:
        self._make(nonce="crashed-session", age_s=TTL * 60 + 120)
        ok, nonce = hl.arm(self.path, TTL)
        self.assertTrue(ok)
        self.assertNotEqual(nonce, "crashed-session")

    def test_arm_nonces_are_unique(self) -> None:
        _, n1 = hl.arm(self.path, TTL)
        os.remove(self.path)
        _, n2 = hl.arm(self.path, TTL)
        self.assertNotEqual(n1, n2)

    # --- beat ---

    def test_beat_own_lease_refreshes_mtime(self) -> None:
        self._make(nonce="mine", age_s=300)
        before = os.path.getmtime(self.path)
        self.assertEqual(hl.beat(self.path, "mine"), 0)
        self.assertGreater(os.path.getmtime(self.path), before)

    def test_beat_gone_reports_operator_stop(self) -> None:
        self.assertEqual(hl.beat(self.path, "mine"), 2)

    def test_beat_foreign_nonce_reports_takeover(self) -> None:
        self._make(nonce="new-owner")
        self.assertEqual(hl.beat(self.path, "mine"), 3)
        # and does NOT delete or touch the foreign lease's content
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "new-owner")

    # --- stop ---

    def test_stop_removes_lease(self) -> None:
        self._make()
        self.assertTrue(hl.stop(self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_stop_idempotent_when_absent(self) -> None:
        self.assertFalse(hl.stop(self.path))

    # --- status ---

    def test_status_absent(self) -> None:
        st = hl.status(self.path, TTL)
        self.assertEqual(st, {"present": False, "age_s": None, "fresh": False, "nonce": None})

    def test_status_fresh_lease(self) -> None:
        self._make(nonce="abc", age_s=60)
        st = hl.status(self.path, TTL)
        self.assertTrue(st["present"])
        self.assertTrue(st["fresh"])
        self.assertEqual(st["nonce"], "abc")
        self.assertGreaterEqual(st["age_s"], 59)

    def test_status_stale_lease(self) -> None:
        self._make(age_s=TTL * 60 + 120)
        st = hl.status(self.path, TTL)
        self.assertTrue(st["present"])
        self.assertFalse(st["fresh"])

    # --- the collision scenarios the whole design exists for ---

    def test_scenario_fresh_session_start_does_not_kill_live_serve(self) -> None:
        """C1: window B's SessionStart clean must not disarm window A's live loop."""
        ok, nonce = hl.arm(self.path, TTL)          # A arms + heartbeats
        self.assertTrue(ok)
        self.assertEqual(hl.clean(self.path, TTL), "fresh")   # B's SessionStart
        self.assertEqual(hl.beat(self.path, nonce), 0)        # A still owns it

    def test_scenario_cross_session_stop_then_relaunch(self) -> None:
        """C4 + takeover race: stop from B, relaunch in B, A's beat sees foreign."""
        ok, nonce_a = hl.arm(self.path, TTL)
        self.assertTrue(ok)
        hl.stop(self.path)                          # B: /wiki-serve --stop
        ok, nonce_b = hl.arm(self.path, TTL)        # B relaunches immediately
        self.assertTrue(ok)
        self.assertEqual(hl.beat(self.path, nonce_a), 3)      # A exits, no delete
        self.assertEqual(hl.beat(self.path, nonce_b), 0)      # B unaffected


if __name__ == "__main__":
    unittest.main(verbosity=2)
