---
name: wiki-log
description: Show recent entries from the wiki's operation log (log.md), optionally filtered by operation type or date. Use when the user runs /wiki-log, asks "what happened in the wiki recently", "show wiki activity", "when did we last ingest X", or wants an audit trail.
---

# wiki-log

Quick viewer for `wiki/log.md`. Read-only.

## Workflow

### 1. Locate wiki/log.md
Find the wiki in the current directory or upward.

### 2. Parse arguments
- `-n <N>` or `--last <N>` — show last N entries (default: 10)
- `--op <type>` — filter by operation: `ingest`, `query-filed`, `lint`, `init`, `capture`, `note`
- `--since <YYYY-MM-DD>` — only entries on or after this date
- `<search-term>` — free-text match against entry titles

### 3. Run
Entries start with a standardized header: `## [YYYY-MM-DD HH:MM] <op> | <title>`.

Default — last 10 entries:
```bash
grep "^## \[" wiki/log.md | tail -10
```

Filter by op type:
```bash
grep "^## \[" wiki/log.md | grep -E "\] <op> \|" | tail -<N>
```

Since a date:
```bash
grep "^## \[" wiki/log.md | awk -F'[][]' -v d="<date>" '$2 >= d' | tail -<N>
```

### 4. Format and show
Present as a clean list with ISO timestamp and title. If the user asks
for details on a specific entry, read the full section from its `## [`
header until the next `## [` or end of file and show the bullet detail.

## Rules
- Read-only — never modify log.md
- Default to 10 entries; users can request more
- If the log is empty, say so clearly
