#!/usr/bin/env python3
"""Classify, filter, format and log one search's candidates for /wiki-corroborate
and /wiki-resolve-contradictions (script audit #9, s142).

Replaces each skill's per-item steps 4.3–4.6 (classify heredoc, filter,
hand-formatted block, coverage-gate math, hand-typed telemetry row) with one
call, and step 4.7's probe + transcribed cost backfill with another.

Per flag (corroborate) or pair (resolve), candidates on stdin, one per line,
`<url> <title>` (the URL has no spaces; the rest of the line is the title):

    python classify_candidates.py <wiki> corroborate --slug <page-slug> \
        --index 3/10 [--include-tier-3] [--batch-id <id>] [--no-log] <<'CANDIDATES'
    https://example.com/docs/x Example docs title
    CANDIDATES

    python classify_candidates.py <wiki> resolve --pair "<slug-a>|<slug-b>" \
        --index 1/4 [--include-tier-3] [--batch-id <id>] [--no-log] <<'CANDIDATES'
    ...
    CANDIDATES

Once per invocation, after the loop (the batch id encodes the window start):

    python classify_candidates.py <wiki> close corroborate|resolve --batch-id <id>

What the per-item call does:
- Reads the flag / pair itself (callout, bucket, confidence, types, dates)
  from the same code as `list_verify_flags.py` / `list_contradictions.py`.
- Classifies each URL with `classify_credibility` (`url_classifier_lists.json`),
  drops `denylist`, drops `tier_3` unless `--include-tier-3`, orders
  tier_1 → tier_2 → tier_3 → unclassified.
- Marks candidates whose URL a page already cites (the `url:` /
  `source_url:` / `source:` of the vault files in its `sources:`).
- Prints the per-item block in the skill's format, then the coverage-gate
  prompt when more than 60% of the shown candidates are unclassified.
- Writes the telemetry row (`append_corroborate_log.py` /
  `append_contradiction_log.py`) unless `--no-log`.

Exit codes: 0 = done; 1 = bad input (unknown slug/pair, malformed line), nothing
logged; 3 = block printed but the telemetry row failed (the `Logged:` line says why).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlsplit

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from extract_page_metadata import _load_credibility_lists, classify_credibility  # noqa: E402
from frontmatter import parse_frontmatter  # noqa: E402
import list_contradictions  # noqa: E402
import list_verify_flags  # noqa: E402

TIER_ORDER = ("tier_1", "tier_2", "tier_3", "unclassified")
TIER_LABEL = {"tier_1": "[tier-1]", "tier_2": "[tier-2]", "tier_3": "[tier-3]",
              "unclassified": "[unclassified — propose tier?]"}
GATE_RATIO = 0.60  # tuned s55 on 29 rows; see wiki-corroborate SKILL § 4.5b
RULE = "═" * 47
IND = " " * 7
URL_KEYS = ("url", "source_url", "source")
COST_FIELDS = [  # (probe JSON key, backfill flag)
    ("total_tokens", "--total-tokens"), ("tool_uses", "--tool-uses"),
    ("duration_ms", "--duration-ms"), ("websearch_calls", "--websearch-calls"),
    ("context_tokens_post_pass", "--context-tokens-post"),
    ("context_tokens_pre_pass", "--context-tokens-pre"),
    ("context_growth", "--context-growth"), ("output_tokens_pass", "--output-tokens-pass"),
]
LOGGERS = {"corroborate": "append_corroborate_log.py", "resolve": "append_contradiction_log.py"}

RESOLVE_FOOTER = """\
───────────────────────────────────────────
Resolution outcomes (HITL — user decides at /wiki-ingest gate):
  [a_wins]     side A's claim stands; aggregator edited with
               `> [!NOTE] Resolved: …` callout citing the new
               source; contradicts: edge removed from both sides
  [b_wins]     mirror — side B's claim stands
  [both_stand] genuine disagreement preserved; contradicts: edge
               downgraded to mutual `## Related` cross-link
               annotated `(disagreement examined — both
               positions stand)`
  [defer]      no decision today; edge stays

Fallback rubric (Jadad four-criterion, when candidates exist
for both sides):
  1. Relevance         — most directly addresses the disputed claim
  2. Recency           — methodology/data is more current
  3. Comprehensiveness — broader coverage of the disputed scope
  4. Low-bias          — cleaner authorship / credibility chain

Default posture (Wikipedia NPOV): describe, don't resolve."""


class InputError(Exception):
    pass


def run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL)


# ------------------------------------------------------------------ candidates


def parse_candidates(text: str) -> list:
    out, seen = [], set()
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, title = line.partition(" ")
        if not re.match(r"^https?://", url, re.I):
            raise InputError(f"candidate line {n}: expected '<url> <title>', got {line[:80]!r}")
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": title.strip().strip('"')})
    return out


def norm_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = (p.netloc or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}".lower()


def cited_urls(wiki: Path, page_path: str) -> set:
    """Normalized URLs of the vault files a page cites in `sources:`."""
    project = wiki.parent
    page = project / page_path
    try:
        sources = parse_frontmatter(page.read_text(encoding="utf-8")).get("sources") or []
    except OSError:
        return set()
    out = set()
    for src in sources if isinstance(sources, list) else []:
        f = project / str(src)
        try:
            fm = parse_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for k in URL_KEYS:
            v = fm.get(k)
            if isinstance(v, str) and v.startswith("http"):
                out.add(norm_url(v))
    return out


def classify(cands: list, include_t3: bool) -> list:
    tiers = _load_credibility_lists()
    kept = []
    for c in cands:
        t = classify_credibility(c["url"], tiers)
        if t == "denylist" or (t == "tier_3" and not include_t3):
            continue
        kept.append({**c, "tier": t})
    kept.sort(key=lambda c: TIER_ORDER.index(c["tier"]))  # stable: search order within a tier
    return kept


def by_tier(kept: list) -> dict:
    return {t: sum(1 for c in kept if c["tier"] == t) for t in TIER_ORDER}


def wrap(label: str, text: str) -> list:
    return textwrap.wrap(f"{label}{text}", width=77, initial_indent=IND, subsequent_indent=IND + "  ",
                         break_on_hyphens=False, break_long_words=False) or [IND + label]


def candidate_lines(kept: list, cited: dict) -> list:
    if not kept:
        return [f"{IND}(no candidates above tier-3 — try --include-tier-3?)"]
    lines = []
    for c in kept:
        lines.append(f"{IND}{TIER_LABEL[c['tier']]} {c['url']}")
        sides = [side for side, urls in cited.items() if norm_url(c["url"]) in urls]
        title = f'"{c["title"]}"' if c["title"] else '""'
        if sides == [""]:
            title += " (already in wiki — this is the source we're trying to corroborate)"
        elif sides:
            title += f" (already in wiki — cited by side {' | '.join(sides)})"
        lines.append(f"{IND}         {title}")
    return lines


def gate_lines(kept: list) -> list:
    total = len(kept)
    unc = [c for c in kept if c["tier"] == "unclassified"]
    if not total or len(unc) / total <= GATE_RATIO:
        return []
    sys.path.insert(0, str(SCRIPTS))
    from _log_writer import domain_from_url
    domains = list(dict.fromkeys(domain_from_url(c["url"]) for c in unc))
    return ["", f"⚠ Classifier coverage gap: {len(unc)} of {total} candidates are unclassified.",
            "Unclassified domains: " + ", ".join(domains),
            "Propose tiers for the unclassified set now? Y/N"]


def log_row(kind: str, wiki: Path, fields: list, kept: list, include_t3: bool, batch: str | None) -> str:
    argv = [sys.executable, str(SCRIPTS / LOGGERS[kind]), "--wiki", str(wiki), *fields,
            "--candidates-returned", str(len(kept)),
            "--candidates-by-tier", json.dumps(by_tier(kept)),
            "--candidate-urls", json.dumps([{"url": c["url"], "tier": c["tier"]} for c in kept]),
            "--include-tier-3-flag", "true" if include_t3 else "false",
            "--skill-version", "corroborate-v2" if kind == "corroborate" else "resolve-contradictions-v2"]
    if batch:
        argv += ["--batch-id", batch]
    if kind == "corroborate":
        argv += ["--user-action", "deferred"]
    r = run(argv)
    if r.returncode != 0:
        return f"Logged: ERROR {LOGGERS[kind]} exit {r.returncode}: {(r.stderr or r.stdout).strip()[:200]}"
    return f"Logged: 1 row ({LOGGERS[kind]}{', batch ' + batch if batch else ''})"


# ------------------------------------------------------------------ item kinds


def corroborate(wiki: Path, a, cands: list) -> tuple:
    flags = [f for f in list_verify_flags._read_pages(wiki) if f["page_slug"] == a.slug.strip("[]").removesuffix(".md")]
    if not flags:
        raise InputError(f"{a.slug!r} is not a verify: true page")
    f = flags[0]
    kept = classify(cands, a.include_tier_3)
    head = [RULE, f"[{a.index}] {f['page_path']}",
            f"{IND}confidence: {f['confidence']} | sources: {f['sources_count']} | bucket: {f['page_bucket']}",
            *wrap("⚠ needs verification: ", f["callout_text"] or "(no inline callout)"), ""]
    body = candidate_lines(kept, {"": cited_urls(wiki, f["page_path"])})
    fields = ["--page-slug", f["page_slug"], "--page-type", str(f["page_type"]),
              "--page-bucket", f["page_bucket"], "--callout-text", f["callout_text"] or ""]
    return head + body, kept, fields


def resolve(wiki: Path, a, cands: list) -> tuple:
    key = "|".join(sorted(s.strip().strip("[]") for s in a.pair.split("|")))
    pairs = [p for p in list_contradictions._collect_pairs(list_contradictions._read_pages(wiki)) if p["pair_key"] == key]
    if not pairs:
        raise InputError(f"no contradicts: pair {key!r}")
    p = pairs[0]
    kept = classify(cands, a.include_tier_3)
    head = [RULE, f"[{a.index}] {p['pair_a_path']}", f"{IND}↕ contradicts ↕", f"{IND}{p['pair_b_path']}",
            f"{IND}shape: {p['shape']} | A: {p['pair_a_last_updated']} | B: {p['pair_b_last_updated']}",
            *wrap("⚠ disagreement: ", p["callout_excerpt"] or "no inline callout — structural edge only"), ""]
    body = candidate_lines(kept, {"A": cited_urls(wiki, p["pair_a_path"]), "B": cited_urls(wiki, p["pair_b_path"])})
    body += [""] + [IND + ln if ln else "" for ln in RESOLVE_FOOTER.splitlines()]
    fields = ["--pair-key", key, "--pair-a-slug", p["pair_a_slug"], "--pair-a-type", str(p["pair_a_type"]),
              "--pair-b-slug", p["pair_b_slug"], "--pair-b-type", str(p["pair_b_type"]),
              "--shape", p["shape"], "--callout-excerpt", p["callout_excerpt"] or ""]
    return head + body, kept, fields


def close(wiki: Path, a) -> int:
    m = re.fullmatch(r".+-(\d+)", a.batch_id.strip())
    if not m:
        raise InputError(f"--batch-id {a.batch_id!r}: expected the <stem>-<start_line> id from --emit-marker")
    probe_argv = [sys.executable, str(SCRIPTS / "session_cost_probe.py"), "--since-line", m.group(1)]
    if a.session_file:
        probe_argv += ["--session-file", a.session_file]
    r = run(probe_argv)
    if r.returncode != 0:
        print(f"close: cost probe failed ({(r.stderr or r.stdout).strip()[:200]}); rows keep null cost (best-effort)")
        return 0
    probe = json.loads(r.stdout)
    argv = [sys.executable, str(SCRIPTS / LOGGERS[a.log]), "--wiki", str(wiki), "--backfill-cost", "--batch-id", a.batch_id]
    for key, flag in COST_FIELDS:
        v = probe.get(key)
        argv += [flag, "null" if v is None else str(v)]
    r = run(argv)
    if r.returncode != 0:
        print(f"close: backfill failed: {(r.stderr or r.stdout).strip()[:300]}")
        return 1
    shown = {k: probe.get(k) for k, _ in COST_FIELDS}
    print(f"close: cost stamped on batch {a.batch_id} — {json.dumps(shown)}")
    return 0


# ------------------------------------------------------------------ driver


def main(argv: list, stdin_text: str | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # cp1252 consoles
    ap = argparse.ArgumentParser(description="Classify + format + log search candidates.")
    ap.add_argument("wiki")
    sub = ap.add_subparsers(dest="kind", required=True)
    for name in ("corroborate", "resolve"):
        sp = sub.add_parser(name)
        if name == "corroborate":
            sp.add_argument("--slug", required=True)
        else:
            sp.add_argument("--pair", required=True, help='"<slug-a>|<slug-b>"')
        sp.add_argument("--index", default="1/1", help="position in the batch, N/M")
        sp.add_argument("--include-tier-3", action="store_true")
        sp.add_argument("--batch-id", default=None)
        sp.add_argument("--candidates-file", default=None, help="read candidates from a file instead of stdin")
        sp.add_argument("--no-log", action="store_true", help="print the block, write no telemetry row")
    sp = sub.add_parser("close")
    sp.add_argument("log", choices=sorted(LOGGERS))
    sp.add_argument("--batch-id", required=True)
    sp.add_argument("--session-file", default=None, help="explicit transcript (default: the live session)")
    a = ap.parse_args(argv)

    wiki = Path(a.wiki).resolve()
    try:
        if not (wiki / "pages").is_dir():
            raise InputError(f"{wiki} has no pages/ directory")
        if a.kind == "close":
            return close(wiki, a)
        if a.candidates_file:
            text = Path(a.candidates_file).read_text(encoding="utf-8")
        elif stdin_text is not None:
            text = stdin_text
        elif sys.stdin is not None and not sys.stdin.isatty():
            if hasattr(sys.stdin, "reconfigure"):
                sys.stdin.reconfigure(encoding="utf-8")
            text = sys.stdin.read()
        else:
            text = ""  # WebSearch failed: zero candidates is a valid row
        cands = parse_candidates(text)
        lines, kept, fields = (corroborate if a.kind == "corroborate" else resolve)(wiki, a, cands)
    except (InputError, OSError) as e:
        print(f"classify_candidates: {e}", file=sys.stderr)
        return 1

    print("\n".join(lines + gate_lines(kept)))
    print()
    print(f"Shown: {len(kept)} of {len(cands)} | by tier: {json.dumps(by_tier(kept))}")
    logged = "Logged: skipped (--no-log)" if a.no_log else log_row(a.kind, wiki, fields, kept, a.include_tier_3, a.batch_id)
    print(logged)
    return 3 if logged.startswith("Logged: ERROR") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
