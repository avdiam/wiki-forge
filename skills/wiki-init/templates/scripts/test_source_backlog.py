"""Unit tests for `lint_wiki.check_source_backlog` content-hash matching.

Regression pin for the phantom-backlog bug (filed 2026-06-03, fixed in the
same change): the established ingest practice leaves a vault file with its
non-canonical clipper-dropped name (spaces, caps, curly apostrophes, parens)
while the source page gets a canonical kebab slug. Under the old stem-only
matcher, every such ingest stayed in `source_backlog` forever — a permanent
phantom that inflated the welcome-report `Backlog:` count by one per ingest.

The fix matches on `source_hash:` (sha256[:16] of the raw vault bytes) first,
falling back to filename-stem. These tests pin that behavior so the phantom
class can't return silently.

Cases:
- hash_match_spacey_filename: spacey vault name, canonical source slug, source
  page carries the file's source_hash → handled (NOT backlog). The phantom case.
- stem_match_fallback: canonical vault name == source slug, no source_hash on
  the page → handled by stem fallback (legacy pages predating source_hash).
- genuinely_unhandled: no stem match and no hash match → IS backlog.
- excluded_paths: files under `.`/`_` top-level segments are never counted.

Run:  python wiki/.scripts/test_source_backlog.py
"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lint_wiki import check_source_backlog


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _source_page(slug: str, source_hash: str | None) -> dict:
    """Minimal `pages` entry mimicking collect_pages() for a source page."""
    fm: dict = {"type": "source"}
    if source_hash is not None:
        fm["source_hash"] = source_hash
    return {
        "path": f"pages/sources/{slug}.md",
        "type": "sources",
        "frontmatter": fm,
        "body": "",
    }


class CheckSourceBacklogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        (self.vault / "Clippings").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _drop(self, relpath: str, content: bytes) -> bytes:
        """Write a raw vault file and return its bytes (for hashing)."""
        p = self.vault / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return content

    def test_hash_match_spacey_filename(self):
        """Spacey vault name + canonical source slug carrying its hash → handled."""
        body = b"---\ntitle: Spacey\n---\n\nArticle body.\n"
        self._drop("Clippings/Some Spacey Article!.md", body)
        pages = {
            "2026-01-01-some-spacey-article": _source_page(
                "2026-01-01-some-spacey-article", _short_hash(body)
            )
        }
        self.assertEqual(
            check_source_backlog(self.vault, pages), [],
            "hash-matched spacey file must not be a phantom backlog entry",
        )

    def test_stem_match_fallback(self):
        """Canonical vault name == source slug, no source_hash → handled by stem."""
        self._drop("Clippings/2026-02-02-legacy.md", b"# Legacy\n")
        pages = {"2026-02-02-legacy": _source_page("2026-02-02-legacy", None)}
        self.assertEqual(
            check_source_backlog(self.vault, pages), [],
            "stem fallback must handle legacy pages lacking source_hash",
        )

    def test_genuinely_unhandled(self):
        """No stem match and no hash match → IS backlog."""
        self._drop("Clippings/Brand New Article.md", b"# New\n")
        # A source page for a DIFFERENT article (different hash, different slug).
        pages = {
            "2026-03-03-other": _source_page(
                "2026-03-03-other", _short_hash(b"unrelated bytes")
            )
        }
        backlog = check_source_backlog(self.vault, pages)
        self.assertEqual(backlog, ["Clippings/Brand New Article.md"])

    def test_excluded_paths_never_counted(self):
        """Files under `.`/`_` top-level segments are excluded regardless of match."""
        (self.vault / ".skipped").mkdir()
        (self.vault / "_mailbox").mkdir()
        self._drop(".skipped/whatever.md", b"# skip\n")
        self._drop("_mailbox/note.md", b"# mail\n")
        self.assertEqual(check_source_backlog(self.vault, {}), [])

    def test_empty_pages_all_unhandled(self):
        """No source pages → every (non-excluded) vault file is backlog."""
        self._drop("Clippings/a.md", b"a")
        self._drop("Clippings/b.md", b"b")
        self.assertEqual(
            sorted(check_source_backlog(self.vault, {})),
            ["Clippings/a.md", "Clippings/b.md"],
        )


if __name__ == "__main__":
    unittest.main()
