#!/usr/bin/env python3
"""Line-preserving frontmatter edits for wiki pages (script audit #8, s142).

The ingestor's per-page frontmatter bookkeeping (add the source, bump
`last_updated`, adjust confidence, grow tags/aliases, typed `contradicts:`
edges, verify-clearing) as one plain command instead of hand Edits. Only the
touched fields change; every other line keeps its exact text.

    python fm_edit.py <wiki> <page> [<page> ...] [ops]

<page> is a slug (`agent-harness`) or a wiki-relative path
(`pages/concepts/agent-harness.md`). Every op applies to every named page, so
one call covers all EDIT pages of an ingest for the shared ops:

    --add-source PATH      append to `sources:` (deduplicated; repeatable)
    --touch                `last_updated:` = today (or --date YYYY-MM-DD)
    --confidence V         set (0.8) or adjust (+0.1 / -0.1); clamped to [0, 1]
    --add-tags a,b         append to `tags:` (deduplicated, case-insensitive)
    --add-aliases a,b      append to `aliases:`
    --set KEY=VALUE        set a scalar field, e.g. status=deprecated (repeatable)
    --contradicts SLUG     add the typed edge on BOTH pages: this page lists
                           [[SLUG]] and SLUG lists this page (repeatable)
    --uncontradict SLUG    remove the edge from both pages; an emptied
                           `contradicts:` field is dropped (repeatable)
    --clear-verify         drop `verify:` and bump confidence one tier (+0.1,
                           capped at 1.0). The body's `⚠ needs verification:`
                           callout is prose: the caller removes it.

All edits are computed first and written only if every page resolves, so a bad
slug changes nothing. Output: one line per changed page naming what changed.
Exit codes: 0 = done (including "no change"); 1 = bad input, nothing written.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

PAGE_TYPES = ("sources", "concepts", "entities", "comparisons", "syntheses")
KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
LINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class EditError(Exception):
    pass


# ------------------------------------------------------------------ page model


class Page:
    """A page split into frontmatter lines and the rest, edited field by field."""

    def __init__(self, path: Path, wiki: Path):
        self.path = path
        self.rel = path.relative_to(wiki).as_posix()
        self.slug = path.stem
        raw = path.read_bytes().decode("utf-8")
        self.nl = "\r\n" if "\r\n" in raw else "\n"
        text = raw.replace("\r\n", "\n")
        if not text.startswith("---\n"):
            raise EditError(f"{self.rel}: no frontmatter block")
        end = text.find("\n---", 3)
        if end == -1:
            raise EditError(f"{self.rel}: unterminated frontmatter block")
        self.fm = text[4:end].split("\n") if end > 4 else []
        self.rest = text[end:]  # starts with "\n---"
        self.original = text
        self.changes: list = []

    # field spans: key line + following indented / list-item lines
    def span(self, key: str) -> tuple | None:
        for i, line in enumerate(self.fm):
            m = KEY_RE.match(line)
            if m and m.group(1) == key:
                j = i + 1
                while j < len(self.fm) and self.fm[j] and (self.fm[j][0] in " \t" or self.fm[j].startswith("- ")):
                    j += 1
                return i, j
        return None

    def value(self, key: str) -> str | None:
        s = self.span(key)
        return None if s is None else KEY_RE.match(self.fm[s[0]]).group(2).strip()

    def replace(self, key: str, lines: list | None) -> None:
        """Replace a field's lines (None removes it); append at the end if absent."""
        s = self.span(key)
        if s is None:
            if lines:
                self.fm.extend(lines)
            return
        self.fm[s[0]:s[1]] = lines or []

    def list_items(self, key: str) -> tuple:
        """(items as raw text, style) where style is 'flow', 'block' or None (absent)."""
        s = self.span(key)
        if s is None:
            return [], None
        head = KEY_RE.match(self.fm[s[0]]).group(2).strip()
        if head.startswith("["):
            inner = head[1:-1] if head.endswith("]") else head[1:]
            return [x.strip() for x in inner.split(",") if x.strip()], "flow"
        if head:
            return [head], "flow"  # a bare scalar: treat as a one-item list
        items = [ln.strip()[2:].strip() for ln in self.fm[s[0] + 1:s[1]] if ln.strip().startswith("- ")]
        return items, "block"

    def block_indent(self, key: str) -> str:
        s = self.span(key)
        if s:
            for ln in self.fm[s[0] + 1:s[1]]:
                if ln.strip().startswith("- "):
                    return ln[: len(ln) - len(ln.lstrip())]
        return "  "

    def text(self) -> str:
        out = "---\n" + "\n".join(self.fm) + self.rest
        return out.replace("\n", self.nl) if self.nl != "\n" else out

    def changed(self) -> bool:
        return self.text().replace(self.nl, "\n") != self.original

    def write(self) -> None:
        self.path.write_bytes(self.text().encode("utf-8"))


def unquote(item: str) -> str:
    item = item.strip()
    if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
        return item[1:-1]
    return item


def fmt_conf(v: float) -> str:
    s = f"{v:.2f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


# ------------------------------------------------------------------ ops


def add_list_items(page: Page, key: str, new: list, label: str) -> None:
    items, style = page.list_items(key)
    seen = {unquote(i).lower() for i in items}
    added = []
    for n in new:
        n = n.strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            added.append(n)
    if not added:
        return
    if style == "block":
        ind = page.block_indent(key)
        s = page.span(key)
        page.fm[s[1]:s[1]] = [f"{ind}- {n}" for n in added]
    else:
        page.replace(key, [f"{key}: [{', '.join(items + added)}]"])
    page.changes.append(f"+{label} " + ", ".join(added))


def add_source(page: Page, src: str) -> None:
    src = src.strip().strip('"').replace("\\", "/")
    items, style = page.list_items("sources")
    if src in {unquote(i) for i in items}:
        return
    if style == "flow" and items:
        page.replace("sources", [f"sources: [{', '.join(items + [src])}]"])
    elif style == "block" and items:
        ind = page.block_indent("sources")
        s = page.span("sources")
        page.fm[s[1]:s[1]] = [f"{ind}- {src}"]
    else:
        page.replace("sources", ["sources:", f"  - {src}"])
    page.changes.append(f"+source {src}")


def set_scalar(page: Page, key: str, value: str) -> None:
    old = page.value(key)
    if old == value:
        return
    page.replace(key, [f"{key}: {value}"])
    page.changes.append(f"{key} {old if old is not None else '(none)'}->{value}")


def adjust_confidence(page: Page, spec: str) -> None:
    old_raw = page.value("confidence")
    try:
        old = float(unquote(old_raw)) if old_raw not in (None, "") else None
    except ValueError:
        raise EditError(f"{page.rel}: confidence is not a number ({old_raw!r})")
    try:
        delta = spec.startswith(("+", "-"))
        v = float(spec)
    except ValueError:
        raise EditError(f"--confidence {spec!r}: expected a number like 0.8 or +0.1")
    if delta:
        if old is None:
            raise EditError(f"{page.rel}: no confidence to adjust by {spec}")
        v = old + v
    v = round(min(1.0, max(0.0, v)), 2)
    if old is not None and round(old, 2) == v:
        return
    page.replace("confidence", [f"confidence: {fmt_conf(v)}"])
    page.changes.append(f"confidence {fmt_conf(old) if old is not None else '(none)'}->{fmt_conf(v)}")


def edges(page: Page) -> list:
    raw = "\n".join(page.fm[slice(*page.span("contradicts"))]) if page.span("contradicts") else ""
    out = []
    for m in LINK_RE.finditer(raw):
        s = m.group(1).strip()
        if s not in out:
            out.append(s)
    return out


def write_edges(page: Page, slugs: list) -> None:
    page.replace("contradicts", [f"contradicts: [{', '.join(chr(34) + '[[' + s + ']]' + chr(34) for s in slugs)}]"]
                 if slugs else None)


def link_edge(page: Page, other: str) -> None:
    cur = edges(page)
    if other not in cur:
        write_edges(page, cur + [other])
        page.changes.append(f"+contradicts {other}")


def unlink_edge(page: Page, other: str) -> None:
    cur = edges(page)
    if other in cur:
        write_edges(page, [s for s in cur if s != other])
        page.changes.append(f"-contradicts {other}")


def clear_verify(page: Page) -> None:
    if page.span("verify") is None:
        return
    old = page.value("verify")
    page.replace("verify", None)
    page.changes.append(f"verify {old}->cleared")
    if page.value("confidence") not in (None, ""):
        adjust_confidence(page, "+0.1")


# ------------------------------------------------------------------ driver


def resolve(wiki: Path, ref: str) -> Path:
    ref = ref.strip().strip('"').replace("\\", "/")
    ref = re.sub(r"^\[\[|\]\]$", "", ref)
    if ref.endswith(".md") or "/" in ref:
        rel = ref[len("wiki/"):] if ref.startswith("wiki/") else ref
        p = (wiki / rel).resolve()
        if p.is_file() and p.is_relative_to(wiki / "pages"):
            return p
        raise EditError(f"no such page: {ref}")
    hits = [wiki / "pages" / t / f"{ref}.md" for t in PAGE_TYPES if (wiki / "pages" / t / f"{ref}.md").is_file()]
    if len(hits) == 1:
        return hits[0].resolve()
    raise EditError(f"no page for slug {ref!r}" if not hits else f"slug {ref!r} is ambiguous: {hits}")


def csv(values: list) -> list:
    return [x.strip() for v in values for x in v.split(",") if x.strip()]


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="Line-preserving frontmatter edits for wiki pages.")
    ap.add_argument("wiki")
    ap.add_argument("pages", nargs="+", help="slugs or wiki-relative paths")
    ap.add_argument("--add-source", action="append", default=[])
    ap.add_argument("--touch", action="store_true")
    ap.add_argument("--date", default=None, help="date for --touch (default today)")
    ap.add_argument("--confidence", default=None)
    ap.add_argument("--add-tags", action="append", default=[])
    ap.add_argument("--add-aliases", action="append", default=[])
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--contradicts", action="append", default=[], metavar="SLUG")
    ap.add_argument("--uncontradict", action="append", default=[], metavar="SLUG")
    ap.add_argument("--clear-verify", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp1252 consoles
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    wiki = Path(a.wiki).resolve()
    try:
        if not (wiki / "pages").is_dir():
            raise EditError(f"{wiki} has no pages/ directory")
        today = a.date or date.today().isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", today):
            raise EditError(f"--date {today!r}: expected YYYY-MM-DD")
        sets = []
        for kv in a.set:
            k, sep, v = kv.partition("=")
            if not sep or not KEY_RE.match(f"{k.strip()}:") or k.strip() in ("sources", "contradicts", "tags", "aliases"):
                raise EditError(f"--set {kv!r}: expected KEY=VALUE for a scalar field")
            sets.append((k.strip(), v.strip()))

        loaded: dict = {}

        def load(path: Path) -> Page:
            if path not in loaded:
                loaded[path] = Page(path, wiki)
            return loaded[path]

        targets = [load(resolve(wiki, ref)) for ref in a.pages]
        links = [(load(resolve(wiki, s)), True) for s in a.contradicts] + \
                [(load(resolve(wiki, s)), False) for s in a.uncontradict]

        for page in targets:
            for src in a.add_source:
                add_source(page, src)
            add_list_items(page, "tags", csv(a.add_tags), "tags")
            add_list_items(page, "aliases", csv(a.add_aliases), "aliases")
            for k, v in sets:
                set_scalar(page, k, v)
            if a.clear_verify:
                clear_verify(page)
            if a.confidence is not None:
                adjust_confidence(page, a.confidence)
            for other, add in links:
                if other is page:
                    raise EditError(f"{page.slug}: a page cannot contradict itself")
                (link_edge if add else unlink_edge)(page, other.slug)
                (link_edge if add else unlink_edge)(other, page.slug)

        if a.touch:
            for page in targets:
                set_scalar(page, "last_updated", today)
            # an inverse edge written to a non-target page is a change there too
            for page in loaded.values():
                if page not in targets and page.changed():
                    set_scalar(page, "last_updated", today)
        changed = [p for p in loaded.values() if p.changed()]
    except EditError as e:
        print(f"fm_edit: {e}\nNothing written.", file=sys.stderr)
        return 1

    if not changed:
        print("fm_edit: no change")
        return 0
    for page in changed:
        page.write()
        print(f"{page.rel}: {'; '.join(page.changes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
