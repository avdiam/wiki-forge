# /wiki-capture — batch mode

> Moved out of SKILL.md (s137, script audit Wave 3) so single captures don't load it. The SKILL's Rules section still applies.

## Batch mode (`--batch <file>`)

When the user has a *list* of URLs rather than one — e.g. a scratch file of
articles to seed — `/wiki-capture --batch <file>` triages the list, then
captures only the URLs the main session actually can.

The list file is plain text: one URL per line, full-line `#` comments allowed
(inline `#` is NOT stripped — URL fragments use it). It must be real URLs, not
article titles.

### 1. Triage the list

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/batch_capture.py" "<file>" --vault vault --json
```

The helper does the deterministic, network-free front-half — dedup against
`vault/_indexes/url-index.json` and domain-classification — and returns four
buckets:

- **`fetchable`** — `head_preserving` / `default` domains. The main session
  captures these (next step). Each carries `classification` + `next_action`.
- **`redirect_to_browser`** — `auth_required` domains (Medium, paywalls, login
  walls). The main session **cannot** fetch these; only the user's
  authenticated browser can (Obsidian Web Clipper).
- **`already_in_vault`** — URL already in the index. Skip.
- **`errored`** — not a parseable http(s) URL, or an intra-file duplicate.

Why the helper stops at classification: capture's fetch+normalize step is
model-driven (WebFetch + body cleaning), not scriptable at quality. The script
owns only what's deterministic; the model owns the fetch.

### 2. Present the buckets

Show the counts + per-bucket URLs as a short summary so the user sees the shape
before anything happens — e.g. `40 → browser, 3 fetchable, 4 already in vault,
1 bad URL`. Then the bucket lists. Do **not** WebFetch an `auth_required` URL;
it returns a paywalled stub.

### 3. Hand the `redirect_to_browser` bucket to the user (clip handoff)

If the bucket is non-empty, hand it over for Obsidian Web Clipper capture via
the shared handoff helper — the same rule every browser-only list uses (drift
re-capture included):

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/clip_handoff.py" --from-json <bucket.json> --title "Batch clip queue <YYYY-MM-DD>"
```

(`<bucket.json>` = the `redirect_to_browser` list from step 1's `--json`
output, saved to `.claude/tmp/`; or pass the URLs as positional args.) Route on
the returned `mode`:

- **`open_tabs`** (≤ 15 URLs) — **offer** to open each URL as a Chrome tab
  (claude-in-chrome `tabs_create_mcp` + `navigate`) so the user can clip them
  one after another. Ask first; open only on a yes. If the extension isn't
  connected, list the URLs instead.
- **`html`** (> 15 URLs) — tell the user the checklist page is at
  `html_path` (numbered links, each opens in a new tab, tick-off checkboxes)
  and that the clips will land in `vault/Clippings/`. Don't open dozens of tabs.

Either way, don't wait inside the batch for the clips — continue with step 4.
The clipped files are picked up later like any other `vault/Clippings/`
arrival (drill commit sweep → `/wiki-triage` / `/wiki-ingest`).

### 4. Capture loop over the `fetchable` bucket (run to exhaustion)

If the bucket is large (>10), surface that first — captures are token-heavy
(WebFetch + normalize each) — and let the user trim or confirm. Then loop the
**normal single-capture pipeline** (Workflow steps 2–5b) over each URL, with
batch-specific behavior:

- **Per-URL error isolation.** A failed fetch / normalize / write does NOT abort
  the batch. Record it as `failed: <url> (<reason>)` and continue to the next.
- **Progress.** Emit a `[i/N] capturing <url>` line per URL so the loop is
  legible.
- **Defer the verification gate.** When a `default` URL is unreachable by urllib
  and falls to `fallback-to-webfetch-with-verification` (Step 3a), do **not**
  pause the loop for the hallucination-check confirmation. Capture it with
  `tag_source: needs-verification`, add it to a deferred list, and keep going.
  `head_preserving` and reachable `default` URLs capture normally.
- Collect the written vault paths and a running tally (captured / failed /
  deferred-for-verification).

### 5. End-of-batch verification review

If any URLs were deferred in step 4, surface them in **one** review pass: show
each captured file + the metadata WebFetch claimed, and let the user
confirm/correct in a single round rather than N interruptions mid-loop. Apply
the same `url_classifier_lists.json` head_preserving / auth_required outcome the
single-capture gate would (Step 3a), only confirmed → updates the file's
`tag_source` and proposes the list edit.

### 6. Hand the captured batch to `/wiki-triage`

Pass the **list** of written vault paths to `/wiki-triage`, which batches them
into ingest groups with the normal per-source approval gates — the right home
for the expensive HITL phase, and how bulk ingests already work. (For a tiny
batch — 1–2 files — chaining straight to `/wiki-ingest` per file is fine.) Then
report the final tally: captured / handed-to-browser / already-in-vault / failed /
deferred-verification.
