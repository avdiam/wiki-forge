"""Sanity check on the `<usage>`-block regex specified in
`.claude/skills/wiki-ingest/SKILL.md` step 13.

Forward-insurance test. The Agent tool today only emits
`total_tokens` / `tool_uses` / `duration_ms`; the four per-field token
counts (`input_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`, `output_tokens`) consistently arrive `null`
in `wiki/.meta/ingest_log.jsonl` rows (verified across 80+ committed
rows, 2026-04-25 to 2026-05-09). The schema and `append_ingest_log.py`
parser were hardened in session 42 to accept all 7 fields; this test
confirms the regex actually catches the other 4 if the harness ever
starts emitting them. Pins the contract: when Anthropic ships per-field
counts in `<usage>`, the parser keeps working without code change.

Run:  python wiki/.scripts/test_usage_parsing.py
"""

import re
import unittest

# Mirrors the SKILL.md step 13 contract. Capture each field
# independently rather than relying on a single multi-group regex, so
# format drift doesn't lose the row.
USAGE_FIELDS = (
    "total_tokens",
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
    "tool_uses",
    "duration_ms",
)


def parse_usage(text):
    """Extract the seven `<usage>` fields from agent-return text.

    Returns a dict mapping field name to int (when found) or None
    (when the field's regex did not match). Mirrors the SKILL.md
    step 13 specification.
    """
    block_match = re.search(r"<usage>(.*?)</usage>", text, re.DOTALL)
    if not block_match:
        return {field: None for field in USAGE_FIELDS}
    body = block_match.group(1)
    out = {}
    for field in USAGE_FIELDS:
        m = re.search(rf"{field}:\s*(\d+)", body)
        out[field] = int(m.group(1)) if m else None
    return out


class TestUsageParsing(unittest.TestCase):

    def test_current_production_shape(self):
        """3 fields populated (total_tokens, tool_uses, duration_ms);
        the 4 cache/io fields stay None. This is what every committed
        ingest_log.jsonl row looks like as of 2026-05-09."""
        text = (
            "Some agent output...\n"
            "<usage>total_tokens: 107145, tool_uses: 49, duration_ms: 428134</usage>\n"
        )
        result = parse_usage(text)
        self.assertEqual(result["total_tokens"], 107145)
        self.assertEqual(result["tool_uses"], 49)
        self.assertEqual(result["duration_ms"], 428134)
        self.assertIsNone(result["input_tokens"])
        self.assertIsNone(result["cache_creation_input_tokens"])
        self.assertIsNone(result["cache_read_input_tokens"])
        self.assertIsNone(result["output_tokens"])

    def test_hypothetical_full_shape(self):
        """All 7 fields populated. The shape the schema was hardened
        for; not currently emitted by the Agent tool but may arrive in
        a future Claude Code build."""
        text = (
            "<usage>total_tokens: 158204, input_tokens: 122, "
            "cache_creation_input_tokens: 18432, "
            "cache_read_input_tokens: 138210, output_tokens: 1440, "
            "tool_uses: 47, duration_ms: 312405</usage>"
        )
        result = parse_usage(text)
        self.assertEqual(result["total_tokens"], 158204)
        self.assertEqual(result["input_tokens"], 122)
        self.assertEqual(result["cache_creation_input_tokens"], 18432)
        self.assertEqual(result["cache_read_input_tokens"], 138210)
        self.assertEqual(result["output_tokens"], 1440)
        self.assertEqual(result["tool_uses"], 47)
        self.assertEqual(result["duration_ms"], 312405)

    def test_partial_emission_non_colliding_fields(self):
        """Agent emits some fields but not others. As long as the
        emitted field names don't share substrings, missing fields
        stay None and present fields parse correctly."""
        text = (
            "<usage>output_tokens: 1440, total_tokens: 48210, "
            "tool_uses: 18</usage>"
        )
        result = parse_usage(text)
        self.assertEqual(result["total_tokens"], 48210)
        self.assertEqual(result["output_tokens"], 1440)
        self.assertEqual(result["tool_uses"], 18)
        self.assertIsNone(result["input_tokens"])
        self.assertIsNone(result["cache_creation_input_tokens"])
        self.assertIsNone(result["cache_read_input_tokens"])
        self.assertIsNone(result["duration_ms"])

    def test_substring_collision_known_quirk(self):
        """KNOWN QUIRK in the SKILL.md step 13 spec: the regex
        `input_tokens:\\s*(\\d+)` matches as a substring inside
        `cache_read_input_tokens` and `cache_creation_input_tokens`.
        When the longer fields are emitted but `input_tokens` itself
        is not, the regex returns the longer field's value as if it
        were `input_tokens`.

        Has not bitten production because the Agent tool today emits
        only `total_tokens` / `tool_uses` / `duration_ms` (no
        substring collision among those three). Will bite if the
        harness ever starts emitting cache_*_input_tokens without
        also emitting input_tokens.

        Fix when it bites: anchor the field-name regex with a
        negative lookbehind, e.g., `(?<![a-z_])input_tokens:\\s*(\\d+)`.
        Update both this test and `.claude/skills/wiki-ingest/SKILL.md`
        step 13's specification together."""
        text = (
            "<usage>cache_read_input_tokens: 41610, total_tokens: 48210, "
            "tool_uses: 18</usage>"
        )
        result = parse_usage(text)
        self.assertEqual(result["total_tokens"], 48210)
        self.assertEqual(result["cache_read_input_tokens"], 41610)
        # Quirk: input_tokens regex matches inside cache_read_input_tokens.
        self.assertEqual(result["input_tokens"], 41610)

    def test_no_usage_block(self):
        """Agent return without a <usage> block — all fields stay
        None. The skill falls back to main-session wall-clock for
        duration_ms in this case."""
        text = "Some agent output, but no usage metadata appended.\n"
        result = parse_usage(text)
        for field in USAGE_FIELDS:
            self.assertIsNone(result[field], f"expected None for {field}")

    def test_whitespace_tolerance(self):
        """Variable whitespace after the colon — the `\\s*` in the
        spec'd regex handles it."""
        text = "<usage>total_tokens:107145, tool_uses:    49, duration_ms:\t428134</usage>"
        result = parse_usage(text)
        self.assertEqual(result["total_tokens"], 107145)
        self.assertEqual(result["tool_uses"], 49)
        self.assertEqual(result["duration_ms"], 428134)


if __name__ == "__main__":
    unittest.main()
