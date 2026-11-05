# Secret-scan findings (wiki-ingest step 10.5)

Pure stdlib regex; no model dependency. Output is JSON with a `findings`
list. Each finding carries `severity` (`high` for known credential
prefixes — GitHub PATs, AWS keys, Stripe / OpenAI / Anthropic keys, etc.;
`low` for ambiguous high-entropy strings inside fenced code blocks),
`pattern`, `line`, `redacted` token preview, and `context` snippet.

**On `high` findings:** surface the redacted match list in the takeaway
block under § Secrets scan with **HALT** semantics — the user must
acknowledge each high finding (`accept all`, `accept N,M`, or describe
how to redact in vault) before dispatch. The upstream prevention is the
capture-side rule: **never store third-party credentials** (API keys,
tokens, session cookies, signed/tokenized URLs) in a vault file — strip
them at capture time, whichever onramp wrote the file. This scan is
forward-insurance for when that rule fails.

**On `low` findings only:** include them in § Secrets scan as a soft
warning — no halt; takeaway-gate proceeds normally. The user can opt to
investigate or skip.

**On no findings:** include § Secrets scan with `clean` for affirmative
visibility; absence-of-section is harder to audit than a positive
all-clear.

This is the parser-side application of [[lethal-trifecta]]'s
"sanitize the untrusted-content leg" mitigation — categorically reliable
for known credential prefixes (where regex is sufficient) but not a
substitute for the capture-side discipline that keeps secrets out of the
vault in the first place.

Sidecar config: `wiki/.meta/secret-patterns.json` (optional) extends the
built-in pattern set; documented in the `check_secrets.py` module
docstring (`python "${CLAUDE_PLUGIN_ROOT}/scripts/check_secrets.py" --help`).
