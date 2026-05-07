#!/usr/bin/env python3
"""Shared parsers over wiki/log.md.

Centralizes the few regex patterns that scan log entries so that
multiple call sites don't drift independently.

Pure stdlib — no external dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path

# Canonical pattern for an ingest log entry header.
# log.md entries look like:  ## [2026-04-15 14:30] ingest | <title>
# The trailing `|` separates op from title and is what distinguishes
# ingest entries from other entry types (lint, capture, query-filed).
INGEST_ENTRY_RE = re.compile(r"^## \[.*?\] ingest \|", re.MULTILINE)


def count_ingests(wiki_root: Path) -> int:
    """Total ingest entries in <wiki>/log.md (lifetime, not since-last-lint).

    Returns 0 if log.md is missing.
    """
    log_path = wiki_root / "log.md"
    if not log_path.is_file():
        return 0
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    return len(INGEST_ENTRY_RE.findall(text))
