---
name: wiki-capture
description: Save new material (URL, file path, or pasted text) to the vault/ folder as clean markdown, then dispatch to the wiki-ingestor subagent to compile it into pages. Use when the user wants to add a new source — e.g. /wiki-capture <url-or-text>, "save this article to the wiki", "capture this page", "add this to my wiki".
---

# wiki-capture

Capture new material into `vault/`, then dispatch to the
`wiki-ingestor` subagent to compile it.

## Input

The user provides one of:

- A URL to an article, paper, or web page
- A file path (PDF, markdown, text)
- Pasted text
- A reference to something from earlier in the conversation ("save that summary")

Plus optional hints: title, tags, category, emphasis areas.

## Workflow

### 1. Locate the wiki

- Look for `wiki/CLAUDE.md` in the current directory or upward
- If no wiki found: tell the user and suggest `/wiki-init`
- Read `wiki/CLAUDE.md` to refresh on schema conventions

### 2. Check for duplicates

Before fetching, check if this source was already captured:

- For URLs: `grep -rl "<url>" vault/` to see if the URL appears in
  any vault file's `source_url:` frontmatter
- For file paths: check if a vault file with matching slug already exists

If a duplicate is found, tell the user and ask whether to re-capture or skip.

### 3. Fetch/read the source

- **URL**: fetch via WebFetch. If paywalled or inaccessible, ask the user
  to paste the text instead — do not write a stub.
- **File path**: read the file.
- **Pasted text**: use what the user provided.
- **Conversation reference**: extract the relevant content from context.

### 4. Normalize to markdown

- Strip navigation, ads, boilerplate, cookie notices, share widgets
- Preserve headings, lists, code blocks, tables, blockquotes
- Keep image alt text; replace image URLs with `[image: description]`
  unless the user asks to download images locally
- Preserve source URLs in links
- Research papers: preserve section structure (abstract, intro, methods, etc.)

### 5. Generate filename

Format: `YYYY-MM-DD-<slug>.md`

- Date: today's ISO date
- Slug: kebab-case, 3–5 words max from the title

Example: `2026-04-15-karpathy-llm-wiki.md`

### 6. Write to `vault/`

Prepend frontmatter:

```yaml
---
source_url: <url if applicable>
source_type: article | paper | gist | blog | docs | transcript | notes | other
captured: <today's date>
title: <source title>
author: <if known>
---
```

Then the cleaned markdown body. Write the file and confirm the path to
the user.

### 7. Dispatch to wiki-ingestor

Tell the user:

> "Captured to `vault/<filename>`. Dispatching to the wiki-ingestor
> subagent for ingestion — it will present key takeaways for your review
> before touching any pages."

Use the Agent tool to spawn a subagent:
- **model:** `"opus"`
- **description:** `"Wiki ingest — <source title>"`
- **prompt:**

```text
You are the wiki-ingestor subagent. Find your full playbook by running
Glob for '**/agents/wiki-ingestor.md' and read it in full before starting.

Context for this run:
- Source: vault/<filename>
- Wiki root: <absolute path to wiki/>
- User hints: <any emphasis/skip guidance the user gave during capture, or "none">

Follow the complete playbook step by step. Respect the >5 contradictions
hard stop. Return a structured report on completion.
```

When the subagent returns, relay its report to the user: pages created,
pages updated, contradictions, new categories, commit hash.

## Rules

- Never skip the frontmatter block on vault files — it's needed for source tracing
- Always normalize web content; raw HTML fragments are not acceptable
- If fetching fails, surface the error and ask for a paste — don't fabricate
- Check for duplicate captures before writing to vault/
- The capture-then-ingest chain is atomic from the user's perspective; don't
  stop halfway without clear communication
- Always dispatch ingestion to the subagent; don't do it inline
