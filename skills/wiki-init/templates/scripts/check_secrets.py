#!/usr/bin/env python3
"""Pre-write secret scanner for /wiki-ingest takeaway gate.

Scans a single source file for credential patterns and high-entropy
strings inside code blocks. Pure stdlib regex; no model dependency.
Output is JSON for the skill to embed in the takeaway block.

Severity levels:
  - high: known credential prefix matched (GitHub PAT, AWS key, Stripe,
    OpenAI, Anthropic). Surface as a hard halt at the takeaway gate —
    user must explicitly acknowledge or remove before dispatch.
  - low: ambiguous high-entropy string (>=40 chars, mixed case + digit)
    inside a fenced code block. Soft warning only.

Pattern set is opt-in expandable via `<wiki>/.meta/secret-patterns.json`
(same shape as `lint-config.json`); see wiki/SPECIFICATION.md § "Secret-pattern config".

Usage:
    python check_secrets.py <path-to-source-file> [--wiki <wiki-root>]

Exit codes:
    0 — no findings
    1 — only `low` findings (soft warnings)
    2 — at least one `high` finding (hard halt)

JSON output (stdout):
    {
      "path": "vault/Web Articles/2026-...md",
      "findings": [
        {"severity": "high", "pattern": "github_pat", "line": 42,
         "redacted": "ghp_xy...", "context": "...token: ghp_xy..."},
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# Built-in patterns. Each: name → compiled regex matching a literal credential.
# Anchored with word-boundary lookbehind to reduce false positives in prose.
BUILTIN_PATTERNS: dict[str, str] = {
    "github_pat_classic": r"\bghp_[A-Za-z0-9]{36}\b",
    "github_pat_oauth":   r"\bgho_[A-Za-z0-9]{36}\b",
    "github_pat_fine":    r"\bgithub_pat_[A-Za-z0-9_]{60,}\b",
    "aws_access_key":     r"\bAKIA[A-Z0-9]{16}\b",
    "stripe_live_secret": r"\bsk_live_[A-Za-z0-9]{24,}\b",
    "stripe_test_secret": r"\bsk_test_[A-Za-z0-9]{24,}\b",
    "openai_api_key":     r"\bsk-(?!ant-)[A-Za-z0-9]{40,}\b",
    "anthropic_api_key":  r"\bsk-ant-[A-Za-z0-9_-]{40,}\b",
    "google_api_key":     r"\bAIza[A-Za-z0-9_-]{35}\b",
    "slack_token":        r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
}

# High-entropy threshold for the soft-warning pass: >=40 chars, contains
# at least one digit and one letter case mixed. Only applied inside fenced
# code blocks to avoid prose false positives.
HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9_+/=-]{40,}\b")


def load_patterns(wiki_root: Path | None) -> dict[str, re.Pattern]:
    patterns = {name: re.compile(rx) for name, rx in BUILTIN_PATTERNS.items()}
    if wiki_root is None:
        return patterns
    sidecar = wiki_root / ".meta" / "secret-patterns.json"
    if not sidecar.is_file():
        return patterns
    try:
        extra = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: could not load {sidecar}: {e}", file=sys.stderr)
        return patterns
    if not isinstance(extra, dict):
        print(f"warning: {sidecar} must be a JSON object {{name: regex}}", file=sys.stderr)
        return patterns
    for name, rx in extra.items():
        if not isinstance(rx, str):
            continue
        try:
            patterns[name] = re.compile(rx)
        except re.error as e:
            print(f"warning: skipping invalid pattern '{name}': {e}", file=sys.stderr)
    return patterns


def redact(match_text: str) -> str:
    if len(match_text) <= 8:
        return match_text[:2] + "..."
    return match_text[:6] + "..." + match_text[-2:]


def context_for(line: str, span: tuple[int, int], width: int = 20) -> str:
    start = max(0, span[0] - width)
    end = min(len(line), span[1] + width)
    snippet = line[start:end].strip()
    return snippet[:80]


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def scan(path: Path, patterns: dict[str, re.Pattern]) -> list[dict]:
    findings: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"error: could not read {path}: {e}", file=sys.stderr)
        return findings
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        high_spans: list[tuple[int, int]] = []
        for name, rx in patterns.items():
            for m in rx.finditer(line):
                redacted = redact(m.group(0))
                preview = line.replace(m.group(0), redacted, 1)
                high_spans.append(m.span())
                findings.append({
                    "severity": "high",
                    "pattern": name,
                    "line": lineno,
                    "redacted": redacted,
                    "context": context_for(preview, (m.start(), m.start() + len(redacted))),
                })
        if in_fence:
            for m in HIGH_ENTROPY_RE.finditer(line):
                token = m.group(0)
                if token.isdigit():
                    continue
                if not (any(c.isalpha() for c in token) and any(c.isdigit() for c in token)):
                    continue
                # Skip if a high-confidence match already covers this span.
                if any(_spans_overlap(m.span(), hs) for hs in high_spans):
                    continue
                findings.append({
                    "severity": "low",
                    "pattern": "high_entropy_in_code_block",
                    "line": lineno,
                    "redacted": redact(token),
                    "context": context_for(line.replace(token, redact(token), 1),
                                           (m.start(), m.start() + len(redact(token)))),
                })
    return findings


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Scan a source file for secret patterns.")
    p.add_argument("source", help="Path to the source file (vault/.../*.md typically)")
    p.add_argument("--wiki", default=None, help="Wiki root (for loading sidecar patterns)")
    args = p.parse_args(argv)

    src = Path(args.source).resolve()
    if not src.is_file():
        print(f"error: {src} not a file", file=sys.stderr)
        return 2

    wiki_root = Path(args.wiki).resolve() if args.wiki else None
    patterns = load_patterns(wiki_root)

    findings = scan(src, patterns)
    print(json.dumps({"path": str(src), "findings": findings}, ensure_ascii=False, indent=2))

    if any(f["severity"] == "high" for f in findings):
        return 2
    if findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
