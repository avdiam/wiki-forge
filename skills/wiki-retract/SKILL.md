---
name: wiki-retract
description: Retract a discredited source from the wiki — marks the source page, cascades confidence reductions and retraction callouts to all citing pages, optionally adds the author to the watchlist. Use when the user says "retract this source", "this article was fake", "demote this author", "remove unreliable source", or names a source/author they no longer trust.
argument-hint: "<source-slug-or-author>"
disable-model-invocation: true
---

# wiki-retract (source discrediting + cascade)

Marks a source as discredited and propagates the retraction through every
wiki page that cites it. Does NOT delete pages — retraction callouts are
more honest than silent disappearance, and `git log` preserves full history.

## Workflow

### 1. Identify the target

Expect one of:

- A source page path: `/wiki-retract pages/sources/2026-04-03-foo.md`
- A source slug: `/wiki-retract 2026-04-03-foo`
- An author name: `/wiki-retract author joe-njenga` (applies to ALL sources by that author)
- A vault file path: `/wiki-retract vault/Web Articles/2026-04-03-foo.md` (resolves to matching source page)

If ambiguous, ask the user to clarify which source(s) to retract.

### 2. Confirm scope with user

Before touching anything, present the blast radius:

```markdown
## Retraction plan for <source title>

**Source page:** pages/sources/<slug>.md
**Reason:** <user-provided or ask>

### Pages that cite this source:
- <page-1> — sole source ⚠ (will be flagged for review)
- <page-2> — 1 of 3 sources (claims from this source will get callout)
- <page-3> — 1 of 5 sources (claims from this source will get callout)

### Proposed actions:
- Mark source page: `retracted: true`, `retraction_reason: "<reason>"`
- Sole-source pages (<N>): confidence → 0.3, verify → true, retraction callout
- Multi-source pages (<N>): retraction callout on claims from this source, confidence reduced by 0.2
- Author watchlist: <add to watchlist? suggest level based on pattern>

Proceed? This is irreversible without manual cleanup.
```

Wait for explicit user confirmation. This is a destructive editorial
operation — never auto-execute.

### 3. Mark the source page

Edit `wiki/pages/sources/<slug>.md` frontmatter:

```yaml
retracted: true
retraction_reason: "<user-provided reason>"
retraction_date: <YYYY-MM-DD>
confidence: 0.1
```

Add a prominent callout at the top of the body (after the frontmatter,
before the first heading):

```markdown
> [!DANGER] RETRACTED
> This source was retracted on <date>. Reason: <reason>.
> Claims originating solely from this source should not be trusted.
> See affected pages below.
```

### 4. Cascade to citing pages

For each wiki page whose `sources:` frontmatter lists the retracted source:

**A. Sole-source pages** (this is the only entry in `sources:`):

1. Set `confidence: 0.3` (or lower if already below 0.3)
2. Set `verify: true`
3. Add callout at top of body:

```markdown
> [!WARNING] Relies on retracted source
> This page's primary source ([[<source-slug>]]) was retracted on <date>:
> "<reason>". All claims on this page need independent verification or
> the page should be removed.
```

4. Add the page to a "needs review" list in the retraction report.

**B. Multi-source pages** (retracted source is one of several):

1. Reduce `confidence` by 0.2 (floor at 0.3)
2. Find the `### From <source title>` subsection under `## Evidence`
   (or wherever claims from this source appear)
3. Wrap that subsection in a callout:

```markdown
> [!WARNING] From retracted source
> The following evidence is from [[<source-slug>]], which was retracted
> on <date>: "<reason>". These claims need independent verification.

<existing content from that source>
```

4. Do NOT remove the claims — wrap them so they're visually demoted but
   still auditable.

### 5. Update indexes

Run `python <wiki>/.scripts/update_index.py <wiki>` to refresh indexes
with updated confidence values.

### 6. Author watchlist (optional)

If the user requested author-level action, or if this is a second retraction
for the same author, propose adding/updating the watchlist:

```markdown
Add <author> to vault/.author-watchlist.md?
- This is their <1st/2nd/Nth> retracted source.
- Proposed level: <caution (1st offense) / untrusted (pattern)>
- This will cause pre_screen.py to flag future articles by this author.

Add? [yes/no/skip]
```

If yes, edit `vault/.author-watchlist.md`:

```markdown
### <author-slug>
- **level:** <caution | untrusted>
- **reason:** <retraction reason + pattern description>
- **since:** <YYYY-MM-DD>
- **affected sources:** <list of retracted source slugs>
```

Update the file's `last_updated:` frontmatter field.

### 7. Commit

```bash
git add wiki/ vault/.author-watchlist.md
git commit -m "retract: <source-slug> — <short reason>"
```

### 8. Log and report

```bash
python <wiki>/.scripts/append_log.py note \
  --title "retract: <source-slug>" \
  --note "<reason>. Affected: <N> sole-source, <M> multi-source pages." \
  --wiki <wiki>
```

Report to user:

```markdown
## Retraction complete

- **Source:** <slug> — marked `retracted: true`
- **Sole-source pages flagged:** <N> (need review/removal)
- **Multi-source pages demoted:** <M> (claims wrapped in callout)
- **Author watchlist:** <added/updated/skipped>
- **Commit:** <hash>

### Pages needing review (sole-source):
- <page-1> — confidence now 0.3, verify: true
- <page-2> — ...

These pages rely entirely on the retracted source. Options:
1. Find an alternative source and re-ingest to rebuild the claims
2. Delete the page if no alternative exists
3. Leave as-is with the retraction warning (honest but low-value)
```

## Undoing a retraction

If a retraction was premature (user changes their mind, new evidence
vindicates the source):

1. Remove `retracted: true`, `retraction_reason`, `retraction_date` from
   the source page frontmatter
2. Remove the `> [!DANGER] RETRACTED` callout from the source page
3. For each affected page: remove the `> [!WARNING]` callouts, restore
   confidence values (check `git log` for pre-retraction values)
4. Update the author watchlist if applicable
5. Commit: `un-retract: <slug> — <reason>`

This is manual by design — un-retracting should require the same deliberate
attention as retracting.

## Rules

- **Never auto-retract.** Every retraction requires explicit user confirmation
  with full blast-radius visibility.
- **Never delete pages.** Demote and flag — let the user decide on removal
  after seeing the damage report.
- **Retraction callouts are permanent until manually removed.** They don't
  auto-clear even if the source confidence is later raised.
- **Author watchlist is advisory, not blocking.** An `untrusted` author's
  future articles still go through the normal pre-screen → takeaway pass →
  approval flow — they just get flagged harder.
