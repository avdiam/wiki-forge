# wiki-librarian — survey mode (`Survey mode: true`)

> Moved out of the librarian playbook (s137, script audit Wave 3) so `/wiki-query` dispatches don't load it. `/wiki-summarize` passes this file's absolute path as `Survey instructions:`. The playbook's step 1, the citation/contradiction/confidence rules of step 3, the footer (step 5) and Rules still apply.

## Step 2: read set

**Survey mode exception.** When the dispatch prompt sets `Survey mode: true`,
the cap and the supplement-with-grep behavior do **not** apply. The dispatching
`/wiki-summarize` skill has already produced the canonical page set via Shape A
(slug-anchored neighbors) or Shape B (top-25 hybrid search) — read the full
dispatched list. Subsetting would distort the audit; supplementing would make
the **Coverage gaps** section dishonest about what's actually in the wiki.

## Step 3: synthesis (replaces the prose body)

When the dispatch prompt sets `Survey mode: true` (i.e., the call came from
`/wiki-summarize`), **replace the prose Q-A body of step 3** with the
five-section topic-coverage template below. The synthesis rules above still
apply — cite every claim with a `[[wikilink]]`, surface contradictions,
flag confidence — but the *shape* of the output is fixed:

```markdown
## Themes
<3–5 sub-areas the wiki has accumulated material on, each with the page slugs
that constitute it. One short paragraph or bulleted list per theme.>

## Agreements
<Cross-source claims that align across two or more pages, with both citations.>

## Disagreements
<Contradictions, open questions, and existing `> [!WARNING]` callouts in
the read pages. Cite both sides when applicable.>

## Coverage gaps
<Pages with `verify: true` (single-source, unverified), `confidence < 0.7`,
or `last_updated` materially older than newer adjacent ingests on the same
theme. Also: sub-areas where the page set thins out — explicit "the wiki
has only one page on X" findings go here.>

## Structural footprint
<Page count of the read set. If `Retrieval shape: slug-anchored` and the
anchor slug is a hub (≥50 inbound), say so with the inbound count. Note
the connected-component placement when relevant. For Shape B, state the
top hybrid_search score range so the user can gauge retrieval confidence.>
```

**Scope override for the dual-onramp coverage-gap protocol.** In survey
mode, "the wiki doesn't cover sub-area X" findings go into the **Coverage
gaps** section as content. **Do not interrupt the survey** with the
`/wiki-capture` + web-search prompts — those are prose-Q-A-mode behavior,
where the answer would otherwise be empty. In survey mode the dispatching
skill has already filtered pages, so there is always *some* coverage to
audit; gaps within that coverage are the survey's job to surface, not a
reason to halt.

**One coverage-gap call still applies in survey mode.** If the read set
is unexpectedly thin (e.g., Shape A returned an orphan with 0 neighbors,
or Shape B's top results are all weak), surface that fact in **Structural
footprint** plainly — don't pad the survey with low-information themes.

## Step 4: offer to file

After presenting the survey, ask:

```text
Would you like me to file this coverage report as
pages/syntheses/<slug>-coverage.md? Coverage reports surface what the wiki
knows about a topic; re-running /wiki-summarize <topic> later will
overwrite this page with the updated audit.
```

`<slug>` is the `Coverage anchor slug` if present (Shape A); otherwise a
kebab-slug of the topic (Shape B). Strip non-alphanumeric chars and
collapse whitespace to dashes — same shape as `/wiki-query`'s synthesis
slugs.

If the user says yes (**create or overwrite** — re-run convention is overwrite):

1. Write `pages/syntheses/<slug>-coverage.md` using the synthesis template from
   AGENTS.md, extended with the coverage-report fields:
   ```yaml
   ---
   title: "<topic> — coverage report"
   type: synthesis
   subtype: coverage-report
   category: <category from anchor or dominant page>
   summary: "<one-sentence audit headline>"
   tags: [coverage-report, <topic-tags>]
   aliases: []
   confidence: 0.7
   sources:
     - vault/<source-1>.md
   coverage_anchor_slug: <slug or null>
   last_updated: <YYYY-MM-DD>
   stale: false
   ---
   ```
   `coverage_anchor_slug:` is the `Coverage anchor slug` from the dispatch
   prompt — set on Shape A, `null` on Shape B. Use `Write` (not `Edit`) so
   re-runs overwrite cleanly.
2. Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/update_index.py" <wiki>`.
3. If git repo, commit:
   ```bash
   cd <wiki>
   git add pages/syntheses/<slug>-coverage.md pages/indexes/ .meta/categories-index.json .meta/index_stats.json
   git commit -m "survey-filed: <slug>-coverage"
   ```
4. Log the operation (reuse the `query-filed` operation type — slug suffix
   `-coverage` and `subtype: coverage-report` frontmatter make the survey
   distinguishable post-hoc without a new log-operation type):
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/append_log.py" query-filed \
     --title "<topic> — coverage report" \
     --created "pages/syntheses/<slug>-coverage" \
     --commit "<short-hash>" \
     --wiki <wiki>
   ```
5. Commit the log entry (it needs the hash, so it can't ride in the first commit):
   ```bash
   git add log.md && git commit -m "query-filed: log entry for <short-hash>"
   ```
   Stage only these paths, never `git add .`: the tree may hold the user's unrelated changes.

If the user says no, do nothing — return the survey as-is.
