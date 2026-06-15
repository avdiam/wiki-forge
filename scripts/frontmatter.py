#!/usr/bin/env python3
"""Shared YAML frontmatter parser for Wiki Forge scripts.

Minimal parser that handles the subset of YAML used by the wiki schema:
scalar strings, numbers, booleans, flow-style lists ([a, b]), and
block-style lists (indented `- item` lines). No external dependencies.

All wiki scripts import from here — this is the single source of truth.
"""

from __future__ import annotations


def parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from a markdown file.

    Returns an empty dict if no valid frontmatter block is found.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip("\n")

    data: dict = {}
    current_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith(" ") or line.startswith("\t"):
            stripped = line.strip()
            if current_key and stripped.startswith("- "):
                if data.get(current_key) is None:
                    data[current_key] = []
                if isinstance(data[current_key], list):
                    data[current_key].append(_coerce(stripped[2:].strip()))
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            data[key] = None
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_coerce(x.strip()) for x in inner.split(",")] if inner else []
        else:
            data[key] = _coerce(value)
    return data


def _coerce(raw: str):
    """Coerce a YAML scalar string to its Python type."""
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    lower = raw.lower()
    if lower in ("true", "false"):
        return lower == "true"
    if lower in ("null", "~", ""):
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw
