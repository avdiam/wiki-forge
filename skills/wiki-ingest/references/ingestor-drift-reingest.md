# wiki-ingestor — drift-triggered re-ingest

> Moved out of the wiki-ingestor playbook (s137, script audit Wave 3) so ingests that don't hit this branch don't load it. The ingestor Reads it from the dispatch's `References:` directory when the branch fires. Every rule here is as binding as the playbook's.

**Drift-triggered re-ingest** — a specific shape of `merge_mode`. Check
the existing source page's frontmatter: if `source_drifted: true` is
present (set by `check_source_freshness.py` when upstream content shifted
from the recorded baseline; `compose_drift_queue.py` lists the re-capture
work — `/wiki-capture` for fetchable URLs, a `clip_handoff.py` handoff to
the user's Web Clipper for auth-required ones), this re-ingest is the closing leg of a
drift cycle and needs three extras on top of the normal merge:

- Title the new section `## Re-ingest <date> — drift re-capture` (not
  just `## Re-ingest <date>`) so the audit trail makes the trigger
  visible to anyone reading the source page later.
- Clear the `source_drifted: true` field from the source page's
  frontmatter, AND remove the `> [!WARNING] Source drift detected`
  callout from the body if present. The drift has been addressed by
  this re-ingest — leaving the flag stale would re-fire `check_drifted_sources`
  lint on every subsequent pass.
- Update `source_hash:` on the source page to the new `sha256_short`
  from the brief. The hash field carries the content-identity of the
  most recently ingested version, not the original capture; the merge
  append handles the version history in the body.

A manual re-ingest of the same source (re-running `/wiki-ingest` on an
unchanged-or-edited vault file without a drift cycle in between) follows
the plain `## Re-ingest <date>` convention — no drift suffix, no
frontmatter cleanup required beyond the normal `last_updated:` bump and
`source_hash:` refresh.
