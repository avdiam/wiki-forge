"""Unit tests for `scale_audit_probes` config-driven re-audit triggers.

Regression pin for the scale-audit skill promotion (session 101). Two things
must hold or the welcome-report `⚙ SCALE AUDIT DUE` banner is useless:

1. **Thresholds are config-driven.** The hardcoded defaults (600 pages, etc.)
   are the 2026-05-08 first-audit values; the live wiki is permanently past
   them. Without a config baseline that each audit resets forward, trigger 1
   would fire on EVERY session forever. `load_thresholds` must merge config
   overrides over defaults, and `check_triggers` must honor them.

2. **Trigger 2 is a composite, not just sustained-nv.** The 2026-06-06 reset
   added the verify-queue arm: a sustained-but-small `needs_verification`
   should NOT fire on its own (the v1.x corroborate drain handles that); the
   audit re-fires only when the latest queue (`needs_verification +
   single_source_unverified`) is genuinely deep.

Cases:
- defaults_when_no_config / overrides_when_config / partial_config_merges
- init_roundtrip: init_config writes, load_thresholds reads back
- trigger1_fires_on_default / trigger1_silenced_by_raised_baseline (the fix)
- trigger2_needs_both_arms: sustained nv alone doesn't fire; deep queue does
- trigger5_concept_hub_only / trigger5_word_threshold
- evaluate_missing_config_uses_defaults / evaluate_with_config

Run:  python dev/tests/run_tests.py   (or this file directly)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import scale_audit_probes as sap


def _probes(total_pages=700, nv_series=None, ssu_series=None, bl_series=None,
            q1=2.0, q4=2.2, top_hubs=None):
    """Build a synthetic probes dict shaped like the real probe output, so
    check_triggers can be exercised without a full wiki fixture."""
    return {
        "inbound": {
            "total_pages": total_pages,
            "top_hubs": top_hubs if top_hubs is not None else [],
        },
        "lint_trend": {
            "series": {
                "needs_verification": nv_series if nv_series is not None else [],
                "single_source_unverified": ssu_series if ssu_series is not None else [],
                "broken_links": bl_series if bl_series is not None else [],
            }
        },
        "ingest_ratio": {
            "quartiles": {
                "Q1_oldest": {"updated_per_created": q1},
                "Q4_newest": {"updated_per_created": q4},
            }
        },
    }


def _fired(result, tid):
    return next(t for t in result["triggers"] if t["id"] == tid)["fired"]


class TestThresholdConfig(unittest.TestCase):
    def test_defaults_when_no_config(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = Path(d)
            self.assertEqual(sap.load_thresholds(wiki), sap.DEFAULT_THRESHOLDS)

    def test_overrides_when_config(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = Path(d)
            (wiki / ".meta").mkdir()
            sap.config_path(wiki).write_text(
                json.dumps({"thresholds": {"total_pages": 800, "verify_queue": 250}}),
                encoding="utf-8",
            )
            t = sap.load_thresholds(wiki)
            self.assertEqual(t["total_pages"], 800)
            self.assertEqual(t["verify_queue"], 250)
            # untouched keys fall back to defaults
            self.assertEqual(t["broken_links"], sap.DEFAULT_THRESHOLDS["broken_links"])

    def test_partial_config_merges(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = Path(d)
            (wiki / ".meta").mkdir()
            sap.config_path(wiki).write_text(
                json.dumps({"thresholds": {"total_pages": 1000}}), encoding="utf-8"
            )
            t = sap.load_thresholds(wiki)
            self.assertEqual(t["total_pages"], 1000)
            self.assertEqual(t["top_concept_hub_words"], sap.DEFAULT_THRESHOLDS["top_concept_hub_words"])

    def test_init_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = Path(d)
            (wiki / ".meta").mkdir()
            rc = sap.init_config(
                wiki, overrides={"total_pages": 800, "verify_queue": 250},
                audit_doc="dev/x-mode7-audit.md", audit_date="2026-06-06", force=True,
            )
            self.assertEqual(rc, 0)
            cfg = sap.load_config(wiki)
            self.assertEqual(cfg["last_audit_date"], "2026-06-06")
            self.assertEqual(cfg["last_audit_doc"], "dev/x-mode7-audit.md")
            self.assertEqual(sap.load_thresholds(wiki)["total_pages"], 800)

    def test_init_no_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            wiki = Path(d)
            (wiki / ".meta").mkdir()
            self.assertEqual(sap.init_config(wiki, force=True), 0)
            self.assertEqual(sap.init_config(wiki, force=False), 2)  # exists, no force


class TestTrigger1(unittest.TestCase):
    def test_trigger1_fires_on_default(self):
        # 673 >= 600 default → fires. This is the stale-default behavior.
        r = sap.check_triggers(_probes(total_pages=673), {}, sap.DEFAULT_THRESHOLDS)
        self.assertTrue(_fired(r, 1))

    def test_trigger1_silenced_by_raised_baseline(self):
        # The core fix: a config baseline at 800 stops the latched 600 from nagging.
        raised = dict(sap.DEFAULT_THRESHOLDS, total_pages=800)
        r = sap.check_triggers(_probes(total_pages=673), {}, raised)
        self.assertFalse(_fired(r, 1))
        # ...and re-fires once genuinely crossed again.
        r2 = sap.check_triggers(_probes(total_pages=812), {}, raised)
        self.assertTrue(_fired(r2, 1))


class TestTrigger2Composite(unittest.TestCase):
    def test_sustained_nv_alone_does_not_fire(self):
        # nv sustained >=100 across 3 runs, but queue (nv only, no ssu) = 110 < 250.
        r = sap.check_triggers(
            _probes(nv_series=[105, 108, 110], ssu_series=[0, 0, 0]),
            {}, sap.DEFAULT_THRESHOLDS,
        )
        self.assertFalse(_fired(r, 2))

    def test_deep_queue_with_sustained_nv_fires(self):
        # nv sustained >=100 AND latest queue = 150 + 110 = 260 >= 250.
        r = sap.check_triggers(
            _probes(nv_series=[120, 130, 150], ssu_series=[100, 105, 110]),
            {}, sap.DEFAULT_THRESHOLDS,
        )
        self.assertTrue(_fired(r, 2))

    def test_deep_queue_without_sustained_nv_does_not_fire(self):
        # latest queue is deep but nv not sustained >=100 across the window.
        r = sap.check_triggers(
            _probes(nv_series=[50, 60, 90], ssu_series=[200, 200, 200]),
            {}, sap.DEFAULT_THRESHOLDS,
        )
        self.assertFalse(_fired(r, 2))


class TestTrigger5ConceptHub(unittest.TestCase):
    def _hub_probes(self, words, hub_type):
        # check_triggers reads the hub file's word count from pages[slug].
        return _probes(top_hubs=[{"slug": "hub", "type": hub_type, "inbound": 99}])

    def test_entity_hub_never_fires(self):
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "hub.md"
            page.write_text("word " * 30000, encoding="utf-8")
            r = sap.check_triggers(
                _probes(top_hubs=[{"slug": "hub", "type": "entities", "inbound": 99}]),
                {"hub": page}, sap.DEFAULT_THRESHOLDS,
            )
            # top_hubs has no concept → trigger 5 finds no concept hub → not fired
            self.assertFalse(_fired(r, 5))

    def test_concept_hub_over_threshold_fires(self):
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "hub.md"
            page.write_text("word " * 26000, encoding="utf-8")
            r = sap.check_triggers(
                _probes(top_hubs=[{"slug": "hub", "type": "concepts", "inbound": 99}]),
                {"hub": page}, sap.DEFAULT_THRESHOLDS,
            )
            self.assertTrue(_fired(r, 5))

    def test_concept_hub_under_threshold_silent(self):
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "hub.md"
            page.write_text("word " * 11000, encoding="utf-8")
            r = sap.check_triggers(
                _probes(top_hubs=[{"slug": "hub", "type": "concepts", "inbound": 99}]),
                {"hub": page}, sap.DEFAULT_THRESHOLDS,
            )
            self.assertFalse(_fired(r, 5))


class TestEvaluate(unittest.TestCase):
    def _make_wiki(self, n_pages, with_config=None):
        d = tempfile.mkdtemp()
        wiki = Path(d)
        (wiki / "CLAUDE.md").write_text("x", encoding="utf-8")
        concepts = wiki / "pages" / "concepts"
        concepts.mkdir(parents=True)
        for i in range(n_pages):
            (concepts / f"p{i}.md").write_text(f"# p{i}\n[[p{(i+1) % n_pages}]]\n", encoding="utf-8")
        (wiki / ".meta").mkdir()
        if with_config is not None:
            sap.init_config(wiki, overrides=with_config, force=True)
        return wiki

    def test_evaluate_missing_config_uses_defaults(self):
        # 5 pages < 600 default → should not fire; configured False.
        wiki = self._make_wiki(5)
        res = sap.evaluate(wiki)
        self.assertFalse(res["should_audit"])
        self.assertFalse(res["configured"])

    def test_evaluate_with_config_reports_metadata(self):
        wiki = self._make_wiki(5, with_config={"total_pages": 800})
        res = sap.evaluate(wiki)
        self.assertTrue(res["configured"])
        self.assertFalse(res["should_audit"])
        self.assertIn("reasons", res)


if __name__ == "__main__":
    unittest.main()
