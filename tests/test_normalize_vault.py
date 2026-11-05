"""Unit tests for `normalize_vault.py` — content-file discovery + naming.

Regression pin for the protocol-file rename bug (found s130, fixed in the same
change): `iter_content_files()` excluded `.`/`_`-prefixed *directories* but not
*files*, and `Path.glob("*.md")` matches dotfiles. A vault-root run therefore
proposed renaming the owner:wiki protocol files (`.wiki-identity.md` ->
`2026-11-05-wiki-identity.md`, …), which would break the SessionStart bootstrap
hook and every `@vault/.wiki-identity.md` include.

Cases:
- vault_root_skips_protocol_files: dot/underscore files at the vault root are
  never yielded; a non-canonical content file in a content dir is.
- excluded_dirs_skipped: `_mailbox/` and `.obsidian/` contents never yielded.
- leaf_dir_skips_dotfiles: pointing at a leaf content dir yields its loose
  content files but not a dotfile inside it.
- normalized_name_from_title: clipper-style title-cased name with a curly
  apostrophe -> date preserved, slug from frontmatter `title:`.
- main_renames_content_only: end-to-end (non-git temp dir) — the content file
  is renamed, the protocol files are untouched.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import sys
import tempfile
import unittest
from pathlib import Path


# Locate the helper-scripts dir in either the live (dev/tests/ -> wiki-forge/scripts)
# or the wiki-forge (tests/ -> scripts) layout, so this file stays byte-identical
# across both repos.
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

import normalize_vault  # noqa: E402

PROTOCOL_FILES = (
    ".wiki-identity.md",
    ".wiki-identity-bootstrap.md",
    ".clipper-rules.md",
    ".clipper-identity.md",
    ".author-watchlist.md",
    "_scratch.md",
)
CLIP_NAME = "2026-10-30-Tracing AI Agents What OTel’s GenAI Spans Actually Capture.md"
CLIP_BODY = (
    "---\n"
    'title: "Tracing AI Agents: What OTel’s GenAI Spans Actually Capture"\n'
    "---\nbody\n"
)
CANON = "2026-10-30-tracing-ai-agents-what-otels-genai-spans-actually-capture.md"


def _build_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "Clippings").mkdir(parents=True)
    (vault / "_mailbox").mkdir()
    (vault / ".obsidian").mkdir()
    for name in PROTOCOL_FILES:
        (vault / name).write_text("---\nowner: wiki\n---\n", encoding="utf-8")
    (vault / "Clippings" / CLIP_NAME).write_text(CLIP_BODY, encoding="utf-8")
    (vault / "Clippings" / ".hidden-note.md").write_text("x", encoding="utf-8")
    (vault / "_mailbox" / "Wiki Message 001.md").write_text("x", encoding="utf-8")
    (vault / ".obsidian" / "Some Note.md").write_text("x", encoding="utf-8")
    return vault


class NormalizeVaultTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = _build_vault(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _names(self, path: Path) -> list[str]:
        return [p.name for p in normalize_vault.iter_content_files(path)]

    def test_vault_root_skips_protocol_files(self):
        names = self._names(self.vault)
        for proto in PROTOCOL_FILES:
            self.assertNotIn(proto, names)
        self.assertIn(CLIP_NAME, names)

    def test_excluded_dirs_skipped(self):
        names = self._names(self.vault)
        self.assertNotIn("Wiki Message 001.md", names)
        self.assertNotIn("Some Note.md", names)

    def test_leaf_dir_skips_dotfiles(self):
        names = self._names(self.vault / "Clippings")
        self.assertEqual(names, [CLIP_NAME])

    def test_normalized_name_from_title(self):
        clip = self.vault / "Clippings" / CLIP_NAME
        self.assertEqual(normalize_vault.normalized_name(clip), CANON)

    def test_main_renames_content_only(self):
        argv = sys.argv
        sys.argv = ["normalize_vault.py", str(self.vault)]
        try:
            rc = normalize_vault.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0)
        self.assertTrue((self.vault / "Clippings" / CANON).exists())
        self.assertFalse((self.vault / "Clippings" / CLIP_NAME).exists())
        for proto in PROTOCOL_FILES:
            self.assertTrue((self.vault / proto).exists(), proto)


if __name__ == "__main__":
    unittest.main()
