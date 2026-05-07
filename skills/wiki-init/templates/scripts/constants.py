#!/usr/bin/env python3
"""Shared constants for Wiki Forge scripts.

Single source of truth for values that multiple scripts need to agree on.
Adding a new wiki page type means editing this file once, not seven.
"""

from __future__ import annotations

# The five wiki page directories under <wiki_root>/. Order matters in some
# call sites (e.g., the Stats section in overview.md presents counts in
# this order); keep `sources` first.
PAGE_TYPES: list[str] = ["sources", "concepts", "entities", "comparisons", "syntheses"]
