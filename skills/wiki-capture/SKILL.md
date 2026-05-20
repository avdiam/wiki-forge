---
name: wiki-capture
description: Save new material (URL, file path, or pasted text) to the vault/ folder as clean markdown, then hand off to the /wiki-ingest skill which orchestrates the rest. Capture is an onramp — it lands raw material in vault/ with proper frontmatter and stops there. It does NOT touch wiki/, does NOT dispatch the wiki-ingestor agent directly. Use when the user wants to add a new source — e.g. /wiki-capture <url-or-text>, "save this article to the wiki", "capture this page", "add this to my wiki".
argument-hint: "<url-or-path-or-paste>"
disable-model-invocation: true
---

# wiki-capture (onramp only)

Capture is the **onramp**. Its single responsibility: land raw material in
`vault/` as clean markdown with proper frontmatter, then hand the path to
`/wiki-ingest` which owns the rest of the pipeline (discovery, analysis,
contradiction probe, takeaway gate, agent dispatch).

This skill does **not** touch `wiki/`. It does **not** dispatch the
`wiki-ingestor` agent. The agent has a single entry point (`/wiki-ingest`)
and capture chains there, not around it.

## Input

The user provides one of:

- A URL to an article, paper, or web page
- A file path (PDF, markdown, text)
- Pasted text
- A reference to something from earlier in the conversation ("save that summary")

Plus optional hints: title, tags, category, emphasis areas. (The hints
don't affect capture; they get passed along to `/wiki-ingest` for the
takeaway pass.)

## Workflow (6 steps)

### 1. Locate the wiki

- Look for `wiki/CLAUDE.md` in the current directory or upward.
- If no wiki found: tell the user and suggest `/wiki-init`.

(No need to read `wiki/AGENTS.md` here — the schema governs what `wiki/`
looks like, and capture doesn't write into `wiki/`. The ingest skill and
the agent both read it later in their own contexts.)

### 2. Check for duplicates (URL-based)

Before fetching, check if this source was already captured:

- For URLs: `grep -rl "<url>" vault/` to see if the URL appears in any vault file's `url:` frontmatter.
- For file paths: check if a vault file with matching slug already exists.

If a duplicate is found, tell the user and ask whether to re-capture or
skip. Re-capture writes a new vault file (typically with today's date as
prefix); skip exits without writing. Note: this is the **URL-based** dedup
check. The stronger **content-hash** dedup probe (against existing wiki
source pages) runs in `/wiki-ingest` step 4 — they're complementary.

### 3. Fetch / read / accept the source

- **URL**: classify and fetch via the cascade below. **Don't go straight to WebFetch** — the metadata-extraction cascade decides which path to take.
- **File path**: read the file. Skip the URL cascade — go to Step 4.
- **Pasted text**: use what the user provided. Skip the URL cascade — go to Step 4.
- **Conversation reference**: extract the relevant content from context. Skip the URL cascade — go to Step 4.

#### 3a. URL classification + metadata cascade

For URL captures, run the classifier first:

```bash
python wiki/.scripts/extract_page_metadata.py "<url>"
```

The script returns a JSON object with `classification`, `next_action`, `metadata`, `tag_source`, and (when applicable) `fetch_status` / `http_code` / `fetch_error`. Route on `next_action`:

**`skip-redirect-to-clipper`** (`classification: auth_required`)
The domain is on the auth-required list — `medium.com` and others known to require an authenticated browser session. WebFetch will return a paywalled stub or fail. **Don't fetch.** Tell the user:

> "`<domain>` is known to require an authenticated browser session (paywall / Cloudflare / login wall). Two options:
> 1. Use the clipper (Chrome browser session) — it's authenticated and can save the article via Obsidian REST API.
> 2. Open the URL yourself, copy the article body, and paste it back here — I'll capture from the paste."

Stop and wait for user direction. Do not write a stub.

**`use-metadata-below`** (`classification: default`, `fetch_status: ok`)
urllib reached the site. Use WebFetch to get the body (its preprocessor handles HTML→markdown cleanly). The `metadata` block in the script output is the source of truth for `source_tags:` — write those values into Step 5's frontmatter. Carry `tag_source:` forward (one of `arxiv-citation` / `page-meta` / `clippings-marker`).

**`fallback-to-webfetch-with-verification`** (`classification: default`, `fetch_status: blocked|error`)
urllib couldn't reach the site (4xx / 5xx / timeout). Try WebFetch with the canonical metadata prompt below (`webfetch_metadata_prompt`). Then **gate the result on user confirmation** — round-2 probe finding (2026-05-08) was that WebFetch's preprocessor sometimes hallucinates metadata when `<head>` is stripped. Surface to the user:

> "urllib couldn't reach `<domain>` (`<fetch_error>`), but WebFetch returned the body and claimed metadata: `<list>`. This may be hallucinated — verify it looks correct. Options:
> 1. Confirm the metadata is real → I'll write `source_tags: [...]` and propose adding `<domain>` to the `head_preserving` list (so future captures from this domain skip the verification gate).
> 2. Reject the metadata → I'll write `source_tags: []` with the `[clippings]` marker, and propose adding `<domain>` to the `auth_required` list (so future captures redirect to clipper)."

Wait for user choice. Apply their decision to `wiki/.scripts/url_classifier_lists.json` only after confirmation. `tag_source: needs-verification` until confirmed; once confirmed, becomes `page-meta` (option 1) or `clippings-marker` (option 2).

**`use-webfetch-prompt`** (`classification: head_preserving`)
The domain is already on the head_preserving list (validated previously via the verification gate). Use WebFetch with the canonical metadata prompt below (`webfetch_metadata_prompt`); trust the returned metadata without re-asking. Carry `tag_source: page-meta` forward.

#### 3a-prompt. Canonical WebFetch metadata prompt

Use this prompt verbatim with WebFetch on `head_preserving` and `fallback-to-webfetch-with-verification` paths. Returns a JSON object the skill can parse the same way it parses `extract_page_metadata.py`'s output.

```
Return ONLY a JSON object with these exact keys (no prose, no markdown):

{
  "og_tags": { "<og:property>": "<value>", ... },
  "json_ld_keywords": [ ... ],
  "article_tag_metas": [ ... ],
  "citation_metas": { "<name>": "<value>", ... },
  "head_content_visible": true | false,
  "body_first_100_chars": "..."
}

Definitions:
- og_tags: any Open Graph meta tags (og:title, og:description, og:type, og:image, og:tag, etc.)
- json_ld_keywords: top-level strings from <script type="application/ld+json"> "keywords" / "about" / "articleSection" fields
- article_tag_metas: any <meta property="article:tag"> or <meta name="article:tag"> values
- citation_metas: any <meta name="citation_*"> tags (Highwire Press — citation_title, citation_author, citation_arxiv_id, citation_doi, citation_date, etc.)
- head_content_visible: true if <head>/<meta> content is clearly readable; false if only the rendered body is available (preprocessor stripped <head>)
- body_first_100_chars: first ~100 chars of the article body to confirm both head + body reachable

If a field is empty, return [] or {} — don't omit. If <head> is stripped, set head_content_visible=false; do NOT infer keywords from body content (this leads to hallucination).
```

Map the response into `source_tags:` the same way Step 5 maps `extract_page_metadata.py` output: dedupe + lowercase across `og_tags` keyword-shaped values + `json_ld_keywords` + `article_tag_metas` + `citation_metas` (citation_subject only, skip identifying fields).

#### 3b. Body fetch (after the cascade)

Independent of which metadata path fired, fetch the rendered body via WebFetch and carry it to Step 4 for normalization. (auth_required is the exception — that branch stops in Step 3a.) The `head_preserving` and `fallback-to-webfetch-with-verification` branches use the same WebFetch call for both body + metadata (the canonical prompt above asks for `body_first_100_chars` as a sanity-check anchor; full body comes from a separate WebFetch call without the JSON-only constraint).

### 4. Normalize to clean markdown

- Strip navigation, ads, boilerplate, cookie notices, share widgets.
- Preserve headings, lists, code blocks, tables, blockquotes.
- Keep image alt text; replace image URLs with `[image: description]` unless the user asks to download images locally.
- Preserve source URLs in links.
- Research papers: preserve section structure (abstract, intro, methods, etc.).

The vault file is immutable once written (per `wiki/AGENTS.md` core
rules); decisions about what to keep happen here, not later.

### 5. Write to `vault/`

Filename: `vault/<dest-dir>/<YYYY-MM-DD>-<slug>.md` (or `vault/<YYYY-MM-DD>-<slug>.md` at root for ad-hoc captures).

- **Destination directory** — pick the folder that matches the source type. The vault is organized by source kind, not by topic; ingest discipline handles topic taxonomy on the wiki side. Current conventions:
  - **`Web Articles/`** — URL captures of blog posts, news articles, Medium pieces, tutorials, vendor docs pages. Symmetric with the clipper's destination (clipper writes here too), so dedup-by-URL works across both onramps.
  - **`Research Papers/`** — arxiv PDFs, conference proceedings, peer-reviewed papers. Convention to formalize on first paper capture; create the dir then if it doesn't exist. The two-step arxiv recipe (clipper-rules Step 7) lands here, not in `Web Articles/`.
  - **Root `vault/`** — pasted text, conversation references, file-path captures, anything without a clean source-type bucket. Acceptable as a catch-all; don't agonize. The URL-index scan picks up root-level files alongside subdir files.
  - **Future dirs** — if a new source type recurs (transcripts, gists, notes), promote it to its own folder when the third instance arrives. Don't pre-create empty dirs.
- **Date**: today's ISO date (the **capture date** — when this file lands in `vault/`). NOT the source's publication date, even if a publication date is plausibly inferable. Capture date is always knowable; publication date often isn't (gists, undated posts, preprint-vs-final, post-publication edits). The wiki schema records publication date separately as an optional `published:` field on the source-summary page if it's reliably knowable.
  - **Manual-drop carve-out:** if the user drops a file into `vault/` with an existing `YYYY-MM-DD-<slug>.md` filename (because the source they pulled it from was auto-named with publication date), do **not** rename to today's date. The capture-date convention applies *when this skill names the file*; manual drops are kept as-is unless the user explicitly requests renaming. This avoids "fixing" filenames that aren't broken and preserves the rare case where the user genuinely wants publication-date filing. (Filename hygiene — apostrophes, spaces, em-dashes — is a separate concern handled at the ingestor gate, not here.)
- **Slug**: kebab-case, 3–5 words from the title.

Example: `vault/Web Articles/2026-04-15-karpathy-llm-wiki.md`

Prepend frontmatter:

```yaml
---
url: <url if applicable>
source_type: article | paper | gist | blog | docs | transcript | notes | other
captured: <today's ISO date>
title: <source title>
author: <if known>
tags: [clippings]
source_tags: [<from Step 3a metadata.og + json_ld_keywords + article_tags + citation_metas, deduped, lowercased>]
tag_source: page-meta | arxiv-citation | clippings-marker | needs-verification
---
```

The `tags: [clippings]` line is a **minimal marker** matching the clipper
convention in `vault/.clipper-rules.md` step 4 — both onramps produce
identically-shaped vault frontmatter. It is deliberately *not* a real
semantic tag list; the wiki ingestor compiles real tags from content
during the takeaway pass. The marker silences a redundant
"no-tags-in-frontmatter" warn reason from `pre_screen.py` so the
load-bearing signal (topic-vocabulary overlap) stays the focus on warn
flows.

**`source_tags:` and `tag_source:` come from Step 3a.** Symmetric with the
clipper-side `source_tags:` field (per `vault/.clipper-rules.md` step 2).
For URL captures, populate both from the cascade output. For file-path /
pasted-text / conversation captures (no live page to scrape), omit
`source_tags:` and `tag_source:` — those fields are URL-only.

How to derive `source_tags:` from the cascade's `metadata` block:
- Take `og:keywords` / `og:tag` values (split on comma if string).
- Concatenate `json_ld_keywords` (already a list).
- Concatenate `article_tags` (already a list).
- For arXiv, concatenate values from `citation_metas` that are tag-shaped
  (e.g., `citation_subject`); skip identifying fields (`citation_doi`,
  `citation_arxiv_id` — those belong as separate top-level frontmatter
  fields when the ingestor produces the source-summary page, not in the
  tag list).
- Dedupe, lowercase, drop empty strings.

If the cascade returned empty metadata or `tag_source: clippings-marker`,
omit `source_tags:` (the field is optional) and set `tag_source: clippings-marker`.

Then the cleaned markdown body. Write the file and confirm the path to
the user.

### 5b. Append to the vault URL index (best-effort)

After the vault write succeeds, append the new file's URL entry to the
precomputed dedup index used by the clipper. **Pass the vault-relative
path you just wrote, verbatim** — the `--append` argument matches the
file's location, so a `Web Articles/` capture passes `Web Articles/<name>`,
a `Research Papers/` capture passes `Research Papers/<name>`, a root
capture passes just `<name>`:

```bash
# Web article capture
python wiki/.scripts/regen_vault_index.py --vault vault --append "Web Articles/<filename>"

# Paper capture
python wiki/.scripts/regen_vault_index.py --vault vault --append "Research Papers/<filename>"

# Root capture (pasted text, conversation ref, etc.)
python wiki/.scripts/regen_vault_index.py --vault vault --append "<filename>"
```

Skip-if-URL-present semantics — duplicate URLs are no-op'd silently. This
is an optimization, not a correctness gate: if the script errors (index
missing, frontmatter empty, race with another writer), log the stderr line
and continue. The wiki-side full regen at the next `/wiki-triage` will
reseat the index regardless. Do not block the handoff to `/wiki-ingest`
on this step.

### 6. Hand off to `/wiki-ingest`

Tell the user:

> "Captured to `vault/<filename>`. Handing off to `/wiki-ingest` for the
> takeaway-and-approval pass — it will discover related pages, analyze
> the source, probe for contradictions, and present a plan for your
> review before any wiki pages are written."

Then invoke the `/wiki-ingest` skill with the captured file's path. The
ingest skill owns everything from here: pre-process, dedup probe by
content hash, pre-screen, discovery, analysis, contradiction probe,
takeaway approval, agent dispatch, telemetry, relay.

If for any reason `/wiki-ingest` is unavailable, tell the user the file
is captured and they can run `/wiki-ingest <path>` manually. Do NOT
dispatch the `wiki-ingestor` agent directly — the agent's contract is
that it receives a complete approved plan, which only `/wiki-ingest`
produces.

## Rules

- **Onramp only.** Capture lands the file in `vault/` and chains to `/wiki-ingest`. It does not dispatch the agent, does not touch `wiki/`, does not run discovery or analysis.
- **Never skip the frontmatter block** on vault files — it's needed for source tracing, the content-hash dedup probe in `/wiki-ingest`, and the agent's source-summary page.
- **Always normalize web content;** raw HTML fragments are not acceptable.
- **Capture date, not publication date,** in the filename and `captured:` field. The two are deliberately distinct.
- **If fetching fails,** surface the error and ask for a paste — don't fabricate.
- **Check for duplicate captures (URL-based) before writing** to `vault/`. The content-hash dedup happens later in `/wiki-ingest` — they're complementary.
- **The capture-then-ingest chain is atomic from the user's perspective**; don't stop halfway without clear communication.
- **`url_classifier_lists.json` updates only with user confirmation.** The fallback-to-WebFetch-with-verification branch proposes `head_preserving` or `auth_required` additions; the user accepts/rejects per session. Never silently mutate the file. Same shape as the takeaway gate — the user owns the durable state, the skill proposes.
- **`source_tags:` is URL-only.** File-path / pasted-text / conversation-reference captures have no live page to scrape; omit the field. The wiki ingestor compiles real tags from content during the takeaway pass — `source_tags:` is just author-attested metadata when available.
