"""Unit tests for the `vault_guard` PreToolUse hook decision core (Subsystem B / P3 v1).

Pins the machine-enforced vault-immutability contract (core rule #1):
- Editing an existing raw source (vault content dir) is DENIED.
- Overwriting an existing raw source via Write is DENIED.
- Creating a NEW vault file via Write is ALLOWED (/wiki-capture / clipper onramp).
- vault protocol/metadata paths (top segment starts with '.' or '_') are ALLOWED
  (owner:wiki identity files, _mailbox/, .obsidian/, .makemd/ — the ingest-exclusion
  carve-out: these are not raw sources).
- Anything outside vault/ is ALLOWED (not the guard's concern).

The hook lives at `.claude/hooks/vault_guard.py` (project-local, not part of the
wiki-forge plugin tree). In a standalone wiki-forge checkout that directory is
absent, so this whole module SKIPS — keeping the file byte-identical across the
dev/tests ↔ wiki-forge/tests mirror without failing in the plugin copy.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import sys
import tempfile
import unittest
from pathlib import Path


def _hooks_dir() -> Path | None:
    here = Path(__file__).resolve()
    cand = here.parents[2] / ".claude" / "hooks"  # live repo: dev/tests -> repo root
    return cand if cand.is_dir() else None


_HOOKS = _hooks_dir()
if _HOOKS is not None:
    sys.path.insert(0, str(_HOOKS))
    import vault_guard as vg  # noqa: E402
else:
    vg = None


@unittest.skipIf(vg is None, ".claude/hooks/ absent (standalone wiki-forge checkout)")
class TestVaultGuardDecide(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        # A real existing raw source, for the overwrite case.
        wa = self.proj / "vault" / "Web Articles"
        wa.mkdir(parents=True)
        self.existing_source = wa / "2026-01-01-existing.md"
        self.existing_source.write_text("raw source body", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _verdict(self, tool: str, rel: str) -> str:
        """decide() on a path expressed relative to the temp project root."""
        fp = str(self.proj / rel)
        return vg.decide(tool, fp, str(self.proj))[0]

    # --- DENY: modifying existing raw sources ---

    def test_edit_raw_source_denied(self) -> None:
        self.assertEqual(self._verdict("Edit", "vault/Web Articles/2026-01-01-existing.md"), "deny")

    def test_notebookedit_raw_source_denied(self) -> None:
        self.assertEqual(self._verdict("NotebookEdit", "vault/Web Articles/x.ipynb"), "deny")

    def test_edit_nested_content_dir_denied(self) -> None:
        self.assertEqual(self._verdict("Edit", "vault/Research Papers/2026/paper.md"), "deny")

    def test_write_overwrite_existing_raw_source_denied(self) -> None:
        self.assertEqual(self._verdict("Write", "vault/Web Articles/2026-01-01-existing.md"), "deny")

    def test_edit_vault_root_content_file_denied(self) -> None:
        self.assertEqual(self._verdict("Edit", "vault/2026-06-16-loose-source.md"), "deny")

    # --- ALLOW: new captures, protocol files, non-vault, edge cases ---

    def test_write_new_vault_file_allowed(self) -> None:
        # Capture / clipper onramp: a NEW file under a content dir is permitted.
        self.assertEqual(self._verdict("Write", "vault/Clippings/2026-06-16-brand-new.md"), "allow")

    def test_edit_protocol_dotfile_allowed(self) -> None:
        self.assertEqual(self._verdict("Edit", "vault/.clipper-rules.md"), "allow")

    def test_edit_mailbox_underscore_allowed(self) -> None:
        self.assertEqual(self._verdict("Edit", "vault/_mailbox/wiki_message_999.md"), "allow")

    def test_edit_obsidian_metadata_allowed(self) -> None:
        self.assertEqual(self._verdict("Write", "vault/.obsidian/app.json"), "allow")

    def test_edit_outside_vault_allowed(self) -> None:
        self.assertEqual(self._verdict("Edit", "wiki/pages/concepts/llm-wiki-pattern.md"), "allow")

    def test_empty_path_allowed(self) -> None:
        self.assertEqual(vg.decide("Edit", "", str(self.proj))[0], "allow")

    def test_deny_reason_is_nonempty(self) -> None:
        verdict, reason = vg.decide(
            "Edit", str(self.proj / "vault" / "Web Articles" / "x.md"), str(self.proj)
        )
        self.assertEqual(verdict, "deny")
        self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
