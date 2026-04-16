#!/usr/bin/env python3
"""Unit tests for Wiki Forge helper scripts.

Uses pytest. Run with: pytest test_scripts.py -v
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Ensure the scripts directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from frontmatter import _coerce, parse_frontmatter


# ============================================================================
# frontmatter.py
# ============================================================================


class TestCoerce:
    def test_integer(self):
        assert _coerce("42") == 42

    def test_negative_integer(self):
        assert _coerce("-7") == -7

    def test_float(self):
        assert _coerce("3.14") == 3.14

    def test_boolean_true(self):
        assert _coerce("true") is True
        assert _coerce("True") is True
        assert _coerce("TRUE") is True

    def test_boolean_false(self):
        assert _coerce("false") is False
        assert _coerce("False") is False

    def test_null(self):
        assert _coerce("null") is None
        assert _coerce("~") is None

    def test_empty_string(self):
        assert _coerce("") is None

    def test_double_quoted_string(self):
        assert _coerce('"hello world"') == "hello world"

    def test_single_quoted_string(self):
        assert _coerce("'hello world'") == "hello world"

    def test_quoted_number_stays_string(self):
        assert _coerce('"42"') == "42"

    def test_plain_string(self):
        assert _coerce("some-slug") == "some-slug"

    def test_string_with_dots_not_float(self):
        assert _coerce("1.2.3") == "1.2.3"

    def test_zero(self):
        assert _coerce("0") == 0

    def test_zero_float(self):
        assert _coerce("0.0") == 0.0


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        assert parse_frontmatter("Just some text") == {}

    def test_no_closing_delimiter(self):
        assert parse_frontmatter("---\ntitle: hello\n") == {}

    def test_basic_scalars(self):
        text = "---\ntitle: My Page\nconfidence: 0.8\nstale: false\n---\nBody"
        fm = parse_frontmatter(text)
        assert fm["title"] == "My Page"
        assert fm["confidence"] == 0.8
        assert fm["stale"] is False

    def test_flow_list(self):
        text = "---\ntags: [ai, ml, deep-learning]\n---\n"
        fm = parse_frontmatter(text)
        assert fm["tags"] == ["ai", "ml", "deep-learning"]

    def test_empty_flow_list(self):
        text = "---\ntags: []\n---\n"
        fm = parse_frontmatter(text)
        assert fm["tags"] == []

    def test_block_list(self):
        text = "---\nsources:\n  - vault/a.md\n  - vault/b.md\n---\n"
        fm = parse_frontmatter(text)
        assert fm["sources"] == ["vault/a.md", "vault/b.md"]

    def test_empty_key_creates_list(self):
        text = "---\nsources:\n---\n"
        fm = parse_frontmatter(text)
        assert fm["sources"] == []

    def test_integer_coercion(self):
        text = "---\ncount: 42\n---\n"
        fm = parse_frontmatter(text)
        assert fm["count"] == 42

    def test_quoted_value(self):
        text = '---\ntitle: "Hello: World"\n---\n'
        fm = parse_frontmatter(text)
        assert fm["title"] == "Hello: World"

    def test_multiline_body_ignored(self):
        text = "---\ntitle: Test\n---\n\n---\nThis is body with dashes"
        fm = parse_frontmatter(text)
        assert fm == {"title": "Test"}

    def test_blank_lines_in_frontmatter(self):
        text = "---\ntitle: Test\n\nconfidence: 0.5\n---\n"
        fm = parse_frontmatter(text)
        assert fm["title"] == "Test"
        assert fm["confidence"] == 0.5

    def test_full_wiki_page_frontmatter(self):
        text = """---
title: Prompt Caching
type: concept
category: llm-concepts
summary: Reusing KV-cache prefixes to reduce inference latency and cost.
tags: [inference, kv-cache, latency, cost-reduction]
aliases: [kv-cache-reuse, prefix-caching]
confidence: 0.85
sources:
  - vault/2026-04-15-article.md
  - vault/2026-04-16-paper.md
last_updated: 2026-04-16
stale: false
---

# Prompt Caching

Body text here.
"""
        fm = parse_frontmatter(text)
        assert fm["title"] == "Prompt Caching"
        assert fm["type"] == "concept"
        assert fm["category"] == "llm-concepts"
        assert fm["tags"] == ["inference", "kv-cache", "latency", "cost-reduction"]
        assert fm["aliases"] == ["kv-cache-reuse", "prefix-caching"]
        assert fm["confidence"] == 0.85
        assert fm["sources"] == ["vault/2026-04-15-article.md", "vault/2026-04-16-paper.md"]
        assert fm["last_updated"] == "2026-04-16"
        assert fm["stale"] is False

    def test_colon_in_value(self):
        text = "---\nsummary: RAG vs Wiki: a comparison\n---\n"
        fm = parse_frontmatter(text)
        assert fm["summary"] == "RAG vs Wiki: a comparison"


# ============================================================================
# hybrid_search.py — BM25, tokenization, RRF, snippets
# ============================================================================

from hybrid_search import (
    BM25,
    STOPWORDS,
    _extract_snippet,
    _tokenize_for_bm25,
    reciprocal_rank_fusion,
)


class TestTokenizeForBM25:
    def test_basic(self):
        tokens = _tokenize_for_bm25("Hello World")
        assert tokens == ["hello", "world"]

    def test_stopwords_removed(self):
        tokens = _tokenize_for_bm25("what is the role of attention")
        assert "what" not in tokens
        assert "is" not in tokens
        assert "the" not in tokens
        assert "of" not in tokens
        assert "role" in tokens
        assert "attention" in tokens

    def test_punctuation_stripped(self):
        tokens = _tokenize_for_bm25("self-attention, multi-head!")
        assert "selfattention" in tokens or "self" in tokens
        assert all(c.isalnum() for t in tokens for c in t)

    def test_single_char_removed(self):
        tokens = _tokenize_for_bm25("I a x test")
        assert "test" in tokens
        assert len([t for t in tokens if len(t) <= 1]) == 0

    def test_empty_string(self):
        assert _tokenize_for_bm25("") == []

    def test_all_stopwords(self):
        assert _tokenize_for_bm25("the is a an") == []


class TestBM25:
    @pytest.fixture
    def simple_bm25(self):
        corpus = [
            ["prompt", "caching", "reduces", "latency"],
            ["rag", "retrieval", "augmented", "generation"],
            ["prompt", "engineering", "techniques"],
            ["vector", "database", "retrieval"],
        ]
        return BM25(corpus)

    def test_exact_term_match_scores_highest(self, simple_bm25):
        scores = simple_bm25.get_scores(["prompt"])
        assert scores[0] > 0
        assert scores[2] > 0
        assert scores[1] == 0
        assert scores[3] == 0

    def test_multi_term_query(self, simple_bm25):
        scores = simple_bm25.get_scores(["prompt", "caching"])
        assert scores[0] > scores[2]

    def test_no_match(self, simple_bm25):
        scores = simple_bm25.get_scores(["nonexistent"])
        assert all(s == 0 for s in scores)

    def test_empty_query(self, simple_bm25):
        scores = simple_bm25.get_scores([])
        assert all(s == 0 for s in scores)

    def test_idf_rare_term_scores_higher(self):
        corpus = [
            ["common", "rare"],
            ["common", "term"],
            ["common", "word"],
        ]
        bm25 = BM25(corpus)
        assert bm25.idf["rare"] > bm25.idf["common"]

    def test_empty_corpus(self):
        bm25 = BM25([])
        assert bm25.corpus_size == 0


class TestReciprocalRankFusion:
    def test_basic_fusion(self):
        dense = [0, 1, 2]
        sparse = [2, 0, 1]
        scores = reciprocal_rank_fusion(dense, sparse)
        assert 0 in scores
        assert 1 in scores
        assert 2 in scores

    def test_agreement_boosts_score(self):
        dense = [0, 1, 2]
        sparse = [0, 1, 2]
        scores = reciprocal_rank_fusion(dense, sparse)
        assert scores[0] > scores[1]
        assert scores[1] > scores[2]

    def test_top_in_both_beats_top_in_one(self):
        dense = [5, 3, 1]
        sparse = [5, 1, 3]
        scores = reciprocal_rank_fusion(dense, sparse)
        assert scores[5] > scores[3]
        assert scores[5] > scores[1]

    def test_single_list(self):
        scores = reciprocal_rank_fusion([0, 1], [])
        assert scores[0] > scores[1]

    def test_k_parameter(self):
        dense = [0, 1]
        sparse = [1, 0]
        scores_k10 = reciprocal_rank_fusion(dense, sparse, k=10)
        scores_k100 = reciprocal_rank_fusion(dense, sparse, k=100)
        assert scores_k10[0] == scores_k10[1]
        assert scores_k100[0] == scores_k100[1]


class TestExtractSnippet:
    def test_term_found(self):
        text = "A " * 50 + "target word here" + " B" * 50
        snippet = _extract_snippet(text, ["target"])
        assert "target" in snippet.lower()

    def test_no_match_returns_start(self):
        text = "abcdef" * 100
        snippet = _extract_snippet(text, ["zzzzz"])
        assert snippet.startswith("abcdef")

    def test_short_text(self):
        snippet = _extract_snippet("Hello", ["hello"])
        assert "Hello" in snippet

    def test_ellipsis_at_start(self):
        text = "prefix " * 50 + "TARGET rest"
        snippet = _extract_snippet(text, ["target"], width=50)
        assert snippet.startswith("...")

    def test_width_respected(self):
        text = "word " * 200
        snippet = _extract_snippet(text, ["word"], width=100)
        stripped = snippet.replace("...", "")
        assert len(stripped) <= 100


# ============================================================================
# lint_wiki.py
# ============================================================================

from lint_wiki import (
    check_broken_links,
    check_broken_sources,
    check_index_drift,
    check_low_confidence,
    check_missing_frontmatter,
    check_orphans,
    check_source_backlog,
    check_stale,
    find_wikilinks,
)


class TestFindWikilinks:
    def test_basic(self):
        assert find_wikilinks("See [[rag]] and [[karpathy]]") == ["rag", "karpathy"]

    def test_with_section(self):
        assert find_wikilinks("[[page#section]]") == ["page"]

    def test_with_alias(self):
        assert find_wikilinks("[[page|display text]]") == ["page"]

    def test_with_section_and_alias(self):
        assert find_wikilinks("[[page#section|display]]") == ["page"]

    def test_no_wikilinks(self):
        assert find_wikilinks("No links here") == []

    def test_multiple_on_same_line(self):
        links = find_wikilinks("Compare [[a]] with [[b]] and [[c]]")
        assert links == ["a", "b", "c"]


class TestCheckOrphans:
    def test_orphan_detected(self):
        pages = {
            "a": {"body": "See [[b]]", "path": "concepts/a.md", "type": "concepts", "frontmatter": {}},
            "b": {"body": "No links", "path": "concepts/b.md", "type": "concepts", "frontmatter": {}},
            "c": {"body": "No links", "path": "concepts/c.md", "type": "concepts", "frontmatter": {}},
        }
        orphans = check_orphans(pages)
        assert "a" in orphans
        assert "c" in orphans
        assert "b" not in orphans

    def test_no_orphans(self):
        pages = {
            "a": {"body": "See [[b]]", "path": "concepts/a.md", "type": "concepts", "frontmatter": {}},
            "b": {"body": "See [[a]]", "path": "concepts/b.md", "type": "concepts", "frontmatter": {}},
        }
        orphans = check_orphans(pages)
        assert orphans == []


class TestCheckBrokenLinks:
    def test_broken_link_detected(self):
        pages = {
            "a": {"body": "See [[nonexistent]]", "path": "concepts/a.md", "type": "concepts", "frontmatter": {}},
        }
        broken = check_broken_links(pages)
        assert len(broken) == 1
        assert broken[0]["target"] == "nonexistent"

    def test_valid_links_pass(self):
        pages = {
            "a": {"body": "See [[b]]", "path": "concepts/a.md", "type": "concepts", "frontmatter": {}},
            "b": {"body": "OK", "path": "concepts/b.md", "type": "concepts", "frontmatter": {}},
        }
        assert check_broken_links(pages) == []


class TestCheckMissingFrontmatter:
    def test_missing_fields(self):
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"title": "A"},
            },
        }
        issues = check_missing_frontmatter(pages)
        assert len(issues) == 1
        assert "type" in issues[0]["missing"]
        assert "title" not in issues[0]["missing"]

    def test_all_present(self):
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {
                    "title": "A",
                    "type": "concept",
                    "category": "test",
                    "summary": "Test",
                    "confidence": 0.8,
                    "sources": ["vault/a.md"],
                    "last_updated": "2026-04-16",
                },
            },
        }
        assert check_missing_frontmatter(pages) == []


class TestCheckStale:
    def test_stale_flag(self):
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"stale": True, "last_updated": "2026-04-16"},
            },
        }
        issues = check_stale(pages)
        assert len(issues) == 1
        assert "stale: true" in issues[0]["reason"]

    def test_old_date(self):
        old_date = (date.today() - timedelta(days=100)).isoformat()
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"stale": False, "last_updated": old_date},
            },
        }
        issues = check_stale(pages)
        assert len(issues) == 1

    def test_recent_date_ok(self):
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"stale": False, "last_updated": date.today().isoformat()},
            },
        }
        assert check_stale(pages) == []


class TestCheckLowConfidence:
    def test_low_confidence(self):
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"confidence": 0.3},
            },
        }
        issues = check_low_confidence(pages)
        assert len(issues) == 1
        assert issues[0]["confidence"] == 0.3

    def test_out_of_range(self):
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"confidence": 1.5},
            },
        }
        issues = check_low_confidence(pages)
        assert len(issues) == 1
        assert "out of range" in issues[0]["reason"]

    def test_good_confidence(self):
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"confidence": 0.8},
            },
        }
        assert check_low_confidence(pages) == []


class TestCheckSourceBacklog:
    def test_backlog_detected(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "2026-04-15-article.md").write_text("content")
        (vault / "2026-04-16-paper.md").write_text("content")
        pages = {
            "2026-04-15-article": {
                "body": "",
                "path": "sources/2026-04-15-article.md",
                "type": "sources",
                "frontmatter": {},
            },
        }
        backlog = check_source_backlog(vault, pages)
        assert "2026-04-16-paper.md" in backlog

    def test_no_backlog(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "article.md").write_text("content")
        pages = {
            "article": {
                "body": "",
                "path": "sources/article.md",
                "type": "sources",
                "frontmatter": {},
            },
        }
        assert check_source_backlog(vault, pages) == []


class TestCheckBrokenSources:
    def test_broken_source(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"sources": ["vault/nonexistent.md"]},
            },
        }
        issues = check_broken_sources(vault, pages)
        assert len(issues) == 1
        assert issues[0]["missing_source"] == "vault/nonexistent.md"

    def test_valid_source(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "article.md").write_text("content")
        pages = {
            "a": {
                "body": "",
                "path": "concepts/a.md",
                "type": "concepts",
                "frontmatter": {"sources": ["vault/article.md"]},
            },
        }
        assert check_broken_sources(vault, pages) == []


class TestCheckIndexDrift:
    def test_missing_index_dir(self, tmp_path):
        result = check_index_drift(tmp_path, {})
        assert result["missing_index"] is True

    def test_page_not_in_index(self, tmp_path):
        indexes = tmp_path / "indexes"
        indexes.mkdir()
        (indexes / "index-concepts.md").write_text("# Concepts\n\n- [[old-page]]")
        pages = {
            "new-page": {"body": "", "path": "concepts/new-page.md", "type": "concepts", "frontmatter": {}},
        }
        result = check_index_drift(tmp_path, pages)
        assert "new-page" in result["in_pages_not_index"]
        assert "old-page" in result["in_index_not_pages"]


# ============================================================================
# append_log.py
# ============================================================================

from append_log import build_entry, format_list, parse_args


class TestFormatList:
    def test_comma_separated(self):
        assert format_list("a, b, c") == ["a", "b", "c"]

    def test_empty_string(self):
        assert format_list("") == []

    def test_whitespace_trimmed(self):
        assert format_list("  a  ,  b  ") == ["a", "b"]

    def test_trailing_comma(self):
        assert format_list("a,b,") == ["a", "b"]


class TestBuildEntry:
    def test_minimal_entry(self):
        args = argparse.Namespace(
            operation="ingest",
            title="Test Source",
            created="",
            updated="",
            contradictions=0,
            commit="",
            note="",
        )
        entry = build_entry(args)
        assert "ingest | Test Source" in entry
        assert "Created" not in entry
        assert "Updated" not in entry

    def test_full_entry(self):
        args = argparse.Namespace(
            operation="ingest",
            title="Paper",
            created="concepts/a.md, entities/b.md",
            updated="concepts/c.md",
            contradictions=3,
            commit="abc1234",
            note="Important update",
        )
        entry = build_entry(args)
        assert "Created: concepts/a.md, entities/b.md" in entry
        assert "Updated: concepts/c.md" in entry
        assert "Contradictions: 3" in entry
        assert "Commit: abc1234" in entry
        assert "Note: Important update" in entry

    def test_zero_contradictions_omitted(self):
        args = argparse.Namespace(
            operation="lint",
            title="Health check",
            created="",
            updated="",
            contradictions=0,
            commit="",
            note="",
        )
        entry = build_entry(args)
        assert "Contradictions" not in entry


# ============================================================================
# ingest_source.py
# ============================================================================

from ingest_source import build_brief, extract_text, slugify


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("What's New?!") == "what-s-new"

    def test_long_truncated(self):
        result = slugify("a" * 100)
        assert len(result) <= 60

    def test_empty_string(self):
        assert slugify("") == "untitled"

    def test_unicode(self):
        result = slugify("Café Résumé")
        assert "caf" in result

    def test_multiple_spaces(self):
        assert slugify("hello   world") == "hello-world"


class TestExtractText:
    def test_markdown_with_title(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# My Title\n\nSome content here.", encoding="utf-8")
        text, title = extract_text(f)
        assert title == "My Title"
        assert "Some content here" in text

    def test_markdown_with_frontmatter_title(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text('---\ntitle: "FM Title"\n---\n\n# Heading\n\nBody.', encoding="utf-8")
        text, title = extract_text(f)
        assert title == "FM Title"

    def test_html_extraction(self, tmp_path):
        f = tmp_path / "test.html"
        f.write_text(
            "<html><head><title>Page Title</title></head>"
            "<body><p>Hello</p><script>evil()</script></body></html>",
            encoding="utf-8",
        )
        text, title = extract_text(f)
        assert title == "Page Title"
        assert "Hello" in text
        assert "evil" not in text

    def test_json_extraction(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        text, title = extract_text(f)
        assert title is None
        assert "key" in text

    def test_csv_extraction(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("a,b,c\n1,2,3\n4,5,6", encoding="utf-8")
        text, title = extract_text(f)
        assert title is None
        assert "a,b,c" in text

    def test_txt_with_h1(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("# Title Line\n\nBody text.", encoding="utf-8")
        text, title = extract_text(f)
        assert title == "Title Line"


class TestBuildBrief:
    def test_new_source(self, tmp_path):
        wiki = tmp_path / "wiki"
        vault = tmp_path / "vault"
        wiki.mkdir()
        vault.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        (wiki / "sources").mkdir()
        source = vault / "2026-04-15-article.md"
        source.write_text("# My Article\n\nContent here with enough words.")

        brief = build_brief(wiki, source)
        assert brief["title_guess"] == "My Article"
        assert brief["ext"] == ".md"
        assert brief["merge_mode"] is False
        assert brief["existing_summary"] is None
        assert brief["suggested_slug"] == "2026-04-15-article"
        assert brief["word_count"] > 0
        assert len(brief["sha256_short"]) == 16
        assert "vault" in brief["source_path"]

    def test_merge_mode(self, tmp_path):
        wiki = tmp_path / "wiki"
        vault = tmp_path / "vault"
        wiki.mkdir()
        vault.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        sources_dir = wiki / "sources"
        sources_dir.mkdir()
        (sources_dir / "existing.md").write_text("---\ntitle: Existing\n---\nOld summary")
        source = vault / "existing.md"
        source.write_text("New content for existing source.")

        brief = build_brief(wiki, source)
        assert brief["merge_mode"] is True
        assert brief["existing_summary"] is not None


# ============================================================================
# graph_analyzer.py
# ============================================================================

from graph_analyzer import analyze, build_graph, find_components
from graph_analyzer import render_human as graph_render_human


class TestFindComponents:
    def test_single_component(self):
        nodes = {"a", "b", "c"}
        out = {"a": {"b"}, "b": {"c"}}
        inp = {"b": {"a"}, "c": {"b"}}
        comps = find_components(nodes, out, inp)
        assert len(comps) == 1
        assert comps[0] == {"a", "b", "c"}

    def test_two_components(self):
        nodes = {"a", "b", "c", "d"}
        out = {"a": {"b"}}
        inp = {"b": {"a"}}
        comps = find_components(nodes, out, inp)
        assert len(comps) >= 2

    def test_isolated_nodes(self):
        nodes = {"x", "y", "z"}
        comps = find_components(nodes, {}, {})
        assert len(comps) == 3
        assert all(len(c) == 1 for c in comps)

    def test_empty_graph(self):
        comps = find_components(set(), {}, {})
        assert comps == []

    def test_components_sorted_by_size(self):
        nodes = {"a", "b", "c", "d", "e"}
        out = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        inp = {"a": {"c"}, "b": {"a"}, "c": {"b"}}
        comps = find_components(nodes, out, inp)
        for i in range(len(comps) - 1):
            assert len(comps[i]) >= len(comps[i + 1])


class TestBuildGraph:
    def test_basic_graph(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        concepts = wiki / "concepts"
        concepts.mkdir()
        (concepts / "rag.md").write_text("---\ntitle: RAG\n---\nSee [[karpathy]]")
        entities = wiki / "entities"
        entities.mkdir()
        (entities / "karpathy.md").write_text("---\ntitle: Karpathy\n---\nCreated [[rag]]")

        nodes, outbound, inbound = build_graph(wiki)
        assert "rag" in nodes
        assert "karpathy" in nodes
        assert "karpathy" in outbound.get("rag", set())
        assert "rag" in outbound.get("karpathy", set())

    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        nodes, out, inp = build_graph(wiki)
        assert len(nodes) == 0


class TestAnalyze:
    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        report = analyze(wiki)
        assert report["total_pages"] == 0
        assert report["total_edges"] == 0

    def test_with_pages(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        concepts = wiki / "concepts"
        concepts.mkdir()
        (concepts / "a.md").write_text("---\ntitle: A\n---\n[[b]] [[c]]")
        (concepts / "b.md").write_text("---\ntitle: B\n---\n[[a]]")
        (concepts / "c.md").write_text("---\ntitle: C\n---\nNo links")

        report = analyze(wiki)
        assert report["total_pages"] == 3
        assert report["total_edges"] == 3
        assert "c" in report["sinks"]


class TestGraphRenderHuman:
    def test_empty_report(self):
        report = {
            "total_pages": 0,
            "total_edges": 0,
            "avg_outbound": 0,
            "avg_inbound": 0,
            "top_hubs_outbound": [],
            "top_hubs_inbound": [],
            "orphans": [],
            "sinks": [],
            "components": [],
            "component_count": 0,
        }
        text = graph_render_human(report)
        assert "0 pages" in text


# ============================================================================
# update_index.py
# ============================================================================

from update_index import _format_entry_line, collect_pages, write_indices, write_stats


class TestFormatEntryLine:
    def test_basic_entry(self):
        entry = {
            "slug": "rag",
            "summary": "Retrieval-Augmented Generation",
            "confidence": 0.8,
            "sources": ["vault/a.md"],
            "stale": False,
            "tags": [],
            "aliases": [],
        }
        line = _format_entry_line(entry)
        assert "[[rag]]" in line
        assert "Retrieval-Augmented Generation" in line
        assert "confidence: 0.8" in line

    def test_with_tags_and_aliases(self):
        entry = {
            "slug": "prompt-caching",
            "summary": "KV-cache reuse",
            "confidence": 0.9,
            "sources": ["vault/a.md", "vault/b.md"],
            "stale": False,
            "tags": ["inference", "cost"],
            "aliases": ["kv-cache-reuse"],
        }
        line = _format_entry_line(entry)
        assert "aka: kv-cache-reuse" in line
        assert "tags: inference, cost" in line
        assert "sources: 2" in line

    def test_stale_flag(self):
        entry = {
            "slug": "old",
            "summary": "Outdated",
            "confidence": 0.5,
            "sources": [],
            "stale": True,
            "tags": [],
            "aliases": [],
        }
        line = _format_entry_line(entry)
        assert "stale" in line

    def test_no_summary(self):
        entry = {
            "slug": "bare",
            "summary": "",
            "confidence": None,
            "sources": [],
            "stale": False,
            "tags": [],
            "aliases": [],
        }
        line = _format_entry_line(entry)
        assert "[[bare]]" in line
        assert "—" not in line


class TestCollectPages:
    def test_collects_from_type_dirs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        concepts = wiki / "concepts"
        concepts.mkdir()
        (concepts / "rag.md").write_text(
            "---\ntitle: RAG\ntype: concept\ncategory: llm-concepts\n"
            "summary: Retrieval stuff\nconfidence: 0.8\nsources: [vault/a.md]\n"
            "last_updated: 2026-04-16\n---\n"
        )
        entities = wiki / "entities"
        entities.mkdir()
        (entities / "karpathy.md").write_text(
            "---\ntitle: Karpathy\ntype: entity\ncategory: people\n"
            "summary: AI researcher\nconfidence: 0.9\nsources: [vault/b.md]\n"
            "last_updated: 2026-04-16\n---\n"
        )

        pages = collect_pages(wiki)
        assert len(pages["concepts"]) == 1
        assert len(pages["entities"]) == 1
        assert pages["concepts"][0]["slug"] == "rag"
        assert pages["entities"][0]["title"] == "Karpathy"


class TestWriteIndices:
    def test_writes_index_files(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")

        pages_by_type = {
            "sources": [],
            "concepts": [
                {
                    "slug": "rag",
                    "title": "RAG",
                    "summary": "Retrieval",
                    "category": "llm-concepts",
                    "confidence": 0.8,
                    "sources": ["vault/a.md"],
                    "tags": ["retrieval"],
                    "aliases": [],
                    "last_updated": "2026-04-16",
                    "stale": False,
                },
            ],
            "entities": [],
            "comparisons": [],
            "syntheses": [],
        }

        files_written, total_pages = write_indices(wiki, pages_by_type)
        assert files_written == 6
        assert total_pages == 1

        master = (wiki / "indexes" / "index.md").read_text()
        assert "index-concepts" in master
        assert "1 pages" in master

        concepts_idx = (wiki / "indexes" / "index-concepts.md").read_text()
        assert "[[rag]]" in concepts_idx
        assert "Retrieval" in concepts_idx


class TestWriteStats:
    def test_writes_stats_file(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        indexes = wiki / "indexes"
        indexes.mkdir()
        (indexes / "index-concepts.md").write_text("- [[rag]] -- test\n" * 10, encoding="utf-8")

        write_stats(wiki, 10)
        stats_path = wiki / ".meta" / "index_stats.json"
        assert stats_path.is_file()
        stats = json.loads(stats_path.read_text())
        assert stats["total_pages"] == 10
        assert stats["estimated_tokens"] > 0


# ============================================================================
# lint_wiki.py — build_report + render (filesystem integration)
# ============================================================================

from lint_wiki import build_report
from lint_wiki import render_human as lint_render_human


class TestBuildReport:
    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        vault = tmp_path / "vault"
        wiki.mkdir()
        vault.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")

        report = build_report(wiki, vault)
        assert report["total_pages"] == 0
        assert report["orphans"] == []

    def test_full_report(self, tmp_path):
        wiki = tmp_path / "wiki"
        vault = tmp_path / "vault"
        wiki.mkdir()
        vault.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")

        concepts = wiki / "concepts"
        concepts.mkdir()
        (concepts / "a.md").write_text(
            "---\ntitle: A\ntype: concept\ncategory: test\n"
            "summary: Page A\nconfidence: 0.8\nsources:\n  - vault/src.md\n"
            "last_updated: 2026-04-16\n---\n\nSee [[nonexistent]]"
        )
        (vault / "src.md").write_text("source content")

        report = build_report(wiki, vault)
        assert report["total_pages"] == 1
        assert len(report["broken_links"]) == 1
        assert report["broken_links"][0]["target"] == "nonexistent"


class TestLintRenderHuman:
    def test_render_produces_string(self):
        report = {
            "wiki_root": "/tmp/wiki",
            "checked_at": "2026-04-16T12:00:00",
            "total_pages": 5,
            "orphans": ["a"],
            "broken_links": [{"page": "concepts/b.md", "target": "missing"}],
            "missing_frontmatter": [],
            "stale": [],
            "low_confidence": [],
            "index_drift": {"missing_index": False, "in_pages_not_index": [], "in_index_not_pages": []},
            "source_backlog": [],
            "broken_sources": [],
        }
        text = lint_render_human(report)
        assert "5" in text
        assert "Orphans" in text
        assert "[[missing]]" in text


# ============================================================================
# hybrid_search.py — filesystem integration (load_pages, check_threshold, search)
# ============================================================================

from hybrid_search import check_threshold, load_pages, search


def _make_wiki_page(directory: Path, slug: str, title: str, summary: str,
                    body: str = "", tags: list[str] | None = None,
                    aliases: list[str] | None = None) -> None:
    tags_str = ", ".join(tags or [])
    aliases_str = ", ".join(aliases or [])
    content = (
        f"---\ntitle: {title}\ntype: concept\ncategory: test\n"
        f"summary: {summary}\nconfidence: 0.8\n"
        f"tags: [{tags_str}]\naliases: [{aliases_str}]\n"
        f"sources:\n  - vault/test.md\nlast_updated: 2026-04-16\n---\n\n{body}"
    )
    directory.mkdir(exist_ok=True)
    (directory / f"{slug}.md").write_text(content, encoding="utf-8")


class TestLoadPages:
    def test_loads_pages(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        _make_wiki_page(wiki / "concepts", "rag", "RAG", "Retrieval approach",
                        tags=["retrieval", "search"], aliases=["rag-pipeline"])

        corpus, metadata = load_pages(wiki)
        assert len(corpus) == 1
        assert len(metadata) == 1
        assert metadata[0]["slug"] == "rag"
        assert metadata[0]["title"] == "RAG"
        assert "retrieval" in corpus[0].lower()
        assert "rag-pipeline" in corpus[0].lower()

    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        corpus, metadata = load_pages(wiki)
        assert corpus == []
        assert metadata == []


class TestCheckThreshold:
    def test_readable_from_stats(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        meta = wiki / ".meta"
        meta.mkdir()
        (meta / "index_stats.json").write_text(
            json.dumps({"estimated_tokens": 500, "total_pages": 10})
        )
        verdict, tokens, pages = check_threshold(wiki)
        assert verdict == "INDEX_READABLE"
        assert tokens == 500
        assert pages == 10

    def test_search_needed(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        meta = wiki / ".meta"
        meta.mkdir()
        (meta / "index_stats.json").write_text(
            json.dumps({"estimated_tokens": 10000, "total_pages": 500})
        )
        verdict, tokens, pages = check_threshold(wiki)
        assert verdict == "SEARCH_NEEDED"

    def test_fallback_scan(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        indexes = wiki / "indexes"
        indexes.mkdir()
        (indexes / "index-concepts.md").write_text("short content")
        verdict, tokens, pages = check_threshold(wiki)
        assert verdict == "INDEX_READABLE"
        assert pages == -1


class TestSearch:
    def test_bm25_search(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        _make_wiki_page(wiki / "concepts", "rag", "RAG",
                        "Retrieval-Augmented Generation for LLMs",
                        body="RAG combines retrieval with generation.")
        _make_wiki_page(wiki / "concepts", "fine-tuning", "Fine-Tuning",
                        "Training a pre-trained model on specific data",
                        body="Fine-tuning adjusts weights.")

        result = search(wiki, "retrieval augmented generation", top_k=5)
        assert result["search_mode"] == "bm25_only"
        assert len(result["results"]) == 2
        assert result["results"][0]["slug"] == "rag"
        assert result["bm25_top_raw_score"] > 0

    def test_empty_wiki_search(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        result = search(wiki, "anything")
        assert result["search_mode"] == "none"
        assert result["results"] == []

    def test_tag_boosting(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        _make_wiki_page(wiki / "concepts", "prompt-caching", "Prompt Caching",
                        "Reusing KV-cache prefixes",
                        tags=["kv-cache", "inference", "latency"],
                        body="Prompt caching technique.")
        _make_wiki_page(wiki / "concepts", "other-topic", "Other Topic",
                        "An unrelated topic about something else",
                        body="Completely different content.")

        result = search(wiki, "kv-cache latency", top_k=5)
        assert result["results"][0]["slug"] == "prompt-caching"

    def test_score_normalization(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "CLAUDE.md").write_text("schema")
        _make_wiki_page(wiki / "concepts", "exact-match", "Exact Match",
                        "This is about exact matching queries")
        _make_wiki_page(wiki / "concepts", "unrelated", "Unrelated",
                        "Completely different domain content about cooking")

        result = search(wiki, "exact matching queries", top_k=5)
        top_score = result["results"][0]["score"]
        bottom_score = result["results"][-1]["score"]
        assert top_score > bottom_score
        assert top_score <= 1.0
        assert bottom_score >= 0.0
