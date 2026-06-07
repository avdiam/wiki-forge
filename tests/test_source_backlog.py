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

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

# Locate the helper-scripts dir in either the live (dev/tests/ -> wiki/.scripts)
# or the wiki-forge (tests/ -> skills/wiki-init/templates/scripts) layout, so
# this file stays byte-identical across both repos.
def _scripts_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (
        here.parents[2] / "wiki" / ".scripts",
        here.parents[1] / "skills" / "wiki-init" / "templates" / "scripts",
    ):
        if cand.is_dir():
            return cand
    raise RuntimeError(f"cannot locate helper-scripts dir from {here}")


SCRIPTS = _scripts_dir()
sys.path.insert(0, str(SCRIPTS))
from lint_wiki import check_source_backlog
import source_backlog as sb


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


class ReadSourceHashTests(unittest.TestCase):
    """The head-read extractor: source_hash from the frontmatter block only."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _page(self, text: str) -> Path:
        p = self.dir / "page.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_extracts_from_frontmatter(self):
        p = self._page("---\ntitle: X\nsource_hash: abc123def4567890\n---\n\nBody.\n")
        self.assertEqual(sb.read_source_hash(p), "abc123def4567890")

    def test_quoted_value(self):
        p = self._page('---\nsource_hash: "abc123def4567890"\n---\nBody\n')
        self.assertEqual(sb.read_source_hash(p), "abc123def4567890")

    def test_ignores_body_mention(self):
        """A `source_hash:` line in the body (after the closing fence) is not read."""
        p = self._page("---\ntitle: X\n---\n\nsource_hash: deadbeefdeadbeef\n")
        self.assertIsNone(sb.read_source_hash(p))

    def test_no_frontmatter(self):
        p = self._page("# Just a heading\n\nNo fence.\n")
        self.assertIsNone(sb.read_source_hash(p))

    def test_missing_field(self):
        p = self._page("---\ntitle: X\ntype: source\n---\nBody\n")
        self.assertIsNone(sb.read_source_hash(p))


class DrillLintParityTests(unittest.TestCase):
    """drill_state.compute_pending must equal len(lint's check_source_backlog)."""

    def test_parity(self):
        import drill_state as ds

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        vault = root / "vault"
        sources = root / "wiki" / "pages" / "sources"
        (vault / "Clippings").mkdir(parents=True)
        sources.mkdir(parents=True)

        ingested = b"---\ntitle: Done\n---\n\nbody\n"
        (vault / "Clippings" / "Spacey Done!.md").write_bytes(ingested)
        h = hashlib.sha256(ingested).hexdigest()[:16]
        (sources / "2026-01-01-done.md").write_text(
            f"---\ntitle: Done\ntype: source\nsource_hash: {h}\n---\n\nbody\n",
            encoding="utf-8",
        )
        (vault / "Clippings" / "Pending One.md").write_bytes(b"# pending\n")

        # lint path (in-memory pages dict)
        pages = {
            "2026-01-01-done": _source_page("2026-01-01-done", h),
        }
        lint_list = check_source_backlog(vault, pages)
        # drill path (disk-read)
        drill_count = ds.compute_pending(vault, sources)

        self.assertEqual(len(lint_list), drill_count, "drill count must equal lint list length")
        self.assertEqual(drill_count, 1, "only the genuinely un-ingested file counts")


if __name__ == "__main__":
    unittest.main()
