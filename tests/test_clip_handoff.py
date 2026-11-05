"""Unit tests for `clip_handoff.py` + `compose_drift_queue.split_buckets` (s130).

Pins the browser-only handoff rule that replaced the clipper mailbox: lists of
`auth_required` URLs go to the user for Obsidian Web Clipper capture —
<= 15 URLs -> `open_tabs` (session offers to open Chrome tabs), > 15 -> `html`
(a checklist page), 0 -> `empty`. Also pins the drift queue's split of drifted
sources into `recapture` (fetchable -> /wiki-capture) and `clip` (auth_required
-> handoff).

Cases:
- decide_thresholds: 0 -> empty, 1 and 15 -> open_tabs, 16 -> html.
- dedupe_preserves_order: duplicates and blanks dropped, first-seen order kept.
- json_input_shapes: list of strings and list of {url: ...} objects both parse.
- html_render_escapes: URLs are HTML-escaped; one <li> per URL.
- main_over_threshold_writes_html: end-to-end CLI writes the checklist to --out.
- main_under_threshold_no_file: <= threshold returns open_tabs and writes nothing.
- drift_split_buckets: medium.com -> clip, a default domain -> recapture.

Run:  python run_tests.py   (from the tests dir; or run this file directly)
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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

import clip_handoff  # noqa: E402
import compose_drift_queue  # noqa: E402


def _run_main(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = clip_handoff.main(argv)
    return rc, json.loads(buf.getvalue())


class ClipHandoffTests(unittest.TestCase):
    def test_decide_thresholds(self):
        self.assertEqual(clip_handoff.decide([]), "empty")
        self.assertEqual(clip_handoff.decide(["u"]), "open_tabs")
        self.assertEqual(clip_handoff.decide([f"u{i}" for i in range(15)]), "open_tabs")
        self.assertEqual(clip_handoff.decide([f"u{i}" for i in range(16)]), "html")
        self.assertEqual(clip_handoff.DEFAULT_THRESHOLD, 15)

    def test_dedupe_preserves_order(self):
        got = clip_handoff.dedupe(["b", " a ", "b", "", "c", "a"])
        self.assertEqual(got, ["b", "a", "c"])

    def test_json_input_shapes(self):
        data = ["https://x/1", {"url": "https://x/2", "domain": "x"}, {"nourl": 1}, 7]
        self.assertEqual(clip_handoff._urls_from_json(data), ["https://x/1", "https://x/2"])
        with self.assertRaises(ValueError):
            clip_handoff._urls_from_json({"url": "x"})

    def test_html_render_escapes(self):
        urls = ['https://m.example/a?x=1&y="2"', "https://m.example/<b>"]
        out = clip_handoff.render_html(urls, "Queue & co")
        self.assertEqual(out.count("<li>"), 2)
        self.assertIn("&amp;y=&quot;2&quot;", out)
        self.assertIn("&lt;b&gt;", out)
        self.assertIn("<title>Queue &amp; co</title>", out)
        self.assertNotIn("<b>", out)

    def test_main_over_threshold_writes_html(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "q.html"
            urls = [f"https://medium.com/p{i}" for i in range(16)]
            rc, res = _run_main(["--out", str(out), "--title", "T", *urls])
            self.assertEqual(rc, 0)
            self.assertEqual(res["mode"], "html")
            self.assertEqual(res["count"], 16)
            self.assertEqual(res["html_path"], str(out))
            self.assertEqual(out.read_text(encoding="utf-8").count("<li>"), 16)

    def test_main_under_threshold_no_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "q.html"
            rc, res = _run_main(["--out", str(out), "https://medium.com/a", "https://medium.com/a"])
            self.assertEqual(rc, 0)
            self.assertEqual(res["mode"], "open_tabs")
            self.assertEqual(res["urls"], ["https://medium.com/a"])
            self.assertIsNone(res["html_path"])
            self.assertFalse(out.exists())


class DriftSplitTests(unittest.TestCase):
    def test_drift_split_buckets(self):
        recs = [
            {"slug": "a", "url": "https://medium.com/@x/post-1", "baseline_at": "", "current_at": ""},
            {"slug": "b", "url": "https://example.org/post-2", "baseline_at": "", "current_at": ""},
        ]
        b = compose_drift_queue.split_buckets(recs)
        self.assertEqual([r["slug"] for r in b["clip"]], ["a"])
        self.assertEqual([r["slug"] for r in b["recapture"]], ["b"])
        body = compose_drift_queue.render_body(b)
        self.assertIn("Re-capture with `/wiki-capture`", body)
        self.assertIn("Clip in the browser", body)
        self.assertNotIn("clipper", body.lower().replace("web clipper", ""))


if __name__ == "__main__":
    unittest.main()
