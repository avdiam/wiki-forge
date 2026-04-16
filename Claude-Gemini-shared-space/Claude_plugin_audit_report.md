# Claude Opus 4.6 — Wiki Forge Plugin Audit Report

**Date:** 2026-04-16
**Scope:** Full codebase review — programmatic testing + architectural + usability assessment
**Verdict:** Ready to use. No blockers. Minor issues documented below.

---

## Testing Results

- **123/123 unit tests pass** (0.59s, Python 3.13, Windows 10)
- All 8 scripts import cleanly, no dependency issues
- End-to-end pipeline verified: generate 50 mock pages -> build index -> BM25 search -> lint -> graph analysis
- Graceful degradation confirmed: BM25-only mode works when ONNX is absent
- Plugin structure valid: `plugin.json` has all required fields, all 6 skills have `SKILL.md`
- Skill-to-agent cross-references verified: every skill references the correct agent, every agent file exists
- `PAGE_TYPES` consistent across all 4 scripts that define it
- No stale directory references (old `pages/` layout), no TODO/FIXME/HACK comments

---

## Minor Issues Found

### 1. Frontmatter parser: flow lists with quoted commas (cosmetic)

**File:** `skills/wiki-init/templates/scripts/frontmatter.py`, `_coerce` / flow list parsing (~line 48)

`tags: ["a, b", c]` parses as `['"a', 'b"', 'c']` instead of `['a, b', 'c']`. The parser splits on commas without respecting quotes inside flow-style YAML lists.

**Impact:** Negligible. The wiki schema only uses simple kebab-case words in tags/aliases (e.g., `[kv-cache, inference, latency]`). No template or documentation uses commas inside tag values. Block-style lists (`- item`) work correctly.

**Fix if desired:** Add quote-aware splitting in the flow list branch. Low priority.

### 2. BM25 score normalization shows steep cliff (by design, but worth noting)

**File:** `skills/wiki-init/templates/scripts/hybrid_search.py`, `search()` (~line 563)

In BM25-only mode, top result scored 0.855 while second scored 0.002 — a 400x cliff. The normalization divides by theoretical maximum (sum of query-term IDFs), which creates a harsh distribution when one doc matches all terms.

**Impact:** Low. The wiki-query skill's 0.3 threshold benefits from this — clear signal separation. In hybrid mode (ONNX), RRF fusion smooths it. Not a bug, just a characteristic to be aware of.

### 3. README uses placeholder repo URL

**File:** `README.md`, line 80

```bash
git clone https://github.com/your-username/wiki-forge.git
```

**Impact:** Cosmetic. Needs updating before publishing.

### 4. No git repository initialized

The project has a `.gitignore` but no `.git/`. The agents are designed to commit after every operation, and the README calls git "your safety net."

**Impact:** Operational. Agents handle the missing-git case gracefully (they check and skip), but git should be initialized before real use.

### 5. Benchmark vault path sharing on Windows

**File:** `test_benchmarks.py`, `generate_mock_wiki()` (~line 46)

When called with `/tmp/test-wiki-audit`, vault is created at `/tmp/vault` (sibling of wiki root). On shared temp directories, parallel test runs could collide.

**Impact:** Test-only. Not production code. Minor inconvenience.

---

## Architecture Strengths Worth Noting

1. **Cost-optimized dispatch:** Small wikis (<200 pages) handled inline, no subagent overhead. Search runs in main session and passes pre-filtered results to librarian.
2. **Conditional search activation:** The 4000-token threshold correctly determines when to switch from index reading to hybrid search. Tested and verified.
3. **Split indices:** Agents read only the sub-index they need, not the full index. Deterministic output (byte-identical on unchanged wikis).
4. **Contradiction hard stop (>5):** A well-designed safety mechanism that prevents wiki corruption from paradigm-shifting sources.
5. **Multi-tool compat:** CLAUDE.md + AGENTS.md dual-write, documented in schema, skill, and README.

---

## Conclusion

The plugin is production-ready for its stated purpose. Code quality exceeds typical v0.1.0 expectations. The only pre-publish actions needed are initializing git and updating the README placeholder URL.
