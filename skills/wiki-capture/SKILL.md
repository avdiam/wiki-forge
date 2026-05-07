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

(No need to read `wiki/CLAUDE.md` here — the schema governs what `wiki/`
looks like, and capture doesn't write into `wiki/`. The ingest skill and
the agent both read it later in their own contexts.)

### 2. Check for duplicates (URL-based)

Before fetching, check if this source was already captured:

- For URLs: `grep -rl "<url>" vault/` to see if the URL appears in any vault file's `source_url:` frontmatter.
- For file paths: check if a vault file with matching slug already exists.

If a duplicate is found, tell the user and ask whether to re-capture or
skip. Re-capture writes a new vault file (typically with today's date as
prefix); skip exits without writing. Note: this is the **URL-based** dedup
check. The stronger **content-hash** dedup probe (against existing wiki
source pages) runs in `/wiki-ingest` step 4 — they're complementary.

### 3. Fetch / read / accept the source

- **URL**: fetch via WebFetch. If paywalled or inaccessible, ask the user to paste the text instead or use the clipper if available — do not write a stub.
- **File path**: read the file.
- **Pasted text**: use what the user provided.
- **Conversation reference**: extract the relevant content from context.

### 4. Normalize to clean markdown

- Strip navigation, ads, boilerplate, cookie notices, share widgets.
- Preserve headings, lists, code blocks, tables, blockquotes.
- Keep image alt text; replace image URLs with `[image: description]` unless the user asks to download images locally.
- Preserve source URLs in links.
- Research papers: preserve section structure (abstract, intro, methods, etc.).

The vault file is immutable once written (per `wiki/CLAUDE.md` core
rules); decisions about what to keep happen here, not later.

### 5. Write to `vault/`

Filename: `vault/<YYYY-MM-DD>-<slug>.md`

- **Date**: today's ISO date (the **capture date** — when this file lands in `vault/`). NOT the source's publication date, even if a publication date is plausibly inferable. Capture date is always knowable; publication date often isn't (gists, undated posts, preprint-vs-final, post-publication edits). The wiki schema records publication date separately as an optional `published:` field on the source-summary page if it's reliably knowable.
  - **Manual-drop carve-out:** if the user drops a file into `vault/` with an existing `YYYY-MM-DD-<slug>.md` filename (because the source they pulled it from was auto-named with publication date), do **not** rename to today's date. The capture-date convention applies *when this skill names the file*; manual drops are kept as-is unless the user explicitly requests renaming. This avoids "fixing" filenames that aren't broken and preserves the rare case where the user genuinely wants publication-date filing. (Filename hygiene — apostrophes, spaces, em-dashes — is a separate concern handled at the ingestor gate, not here.)
- **Slug**: kebab-case, 3–5 words from the title.

Example: `vault/2026-04-15-karpathy-llm-wiki.md`

Prepend frontmatter:

```yaml
---
source_url: <url if applicable>
source_type: article | paper | gist | blog | docs | transcript | notes | other
captured: <today's ISO date>
title: <source title>
author: <if known>
tags: [clippings]
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

Then the cleaned markdown body. Write the file and confirm the path to
the user.

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
