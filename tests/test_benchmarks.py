#!/usr/bin/env python3
"""Test and benchmarking tools for Wiki Forge.

Commands:
  generate <wiki-root> <num-pages>   Generate mock wiki pages for testing
  metric <wiki-root>                 Report index token costs and page counts
  search_latency <wiki-root> <query> Time a hybrid search query
  threshold <wiki-root>              Check the conditional search threshold

Usage:
  python test_benchmarks.py generate /path/to/wiki 100
  python test_benchmarks.py metric /path/to/wiki
  python test_benchmarks.py search_latency /path/to/wiki "prompt caching"
  python test_benchmarks.py threshold /path/to/wiki
"""

import argparse
import random
import subprocess
import sys
import time
from pathlib import Path

TOPICS = [
    "Machine Learning", "Neural Networks", "Gradient Descent", "Transformer Architectures",
    "Backpropagation", "Agentic Systems", "Retrieval Augmented Generation", "Prompt Engineering",
    "Zero Shot Learning", "Few Shot Prompting", "Reinforcement Learning", "Vector Databases",
    "Semantic Search", "Reciprocal Rank Fusion", "TF-IDF Search", "BM25 Algorithm",
    "Hardware Acceleration", "CUDA Cores", "Quantum Computing Basics", "Graph Theory",
    "Attention Mechanisms", "Context Windows", "Fine Tuning", "RLHF Training",
    "Embedding Models", "Knowledge Graphs", "Prompt Caching", "Model Distillation",
    "Chain of Thought", "Tool Use Patterns", "Multi Agent Systems", "Code Generation",
]

CATEGORIES = ["ai-concepts", "ml-methods", "nlp-techniques", "infrastructure", "evaluation"]


def generate_mock_wiki(wiki_root_str: str, num_pages: int):
    """Generate mock concept pages for load testing.

    Creates the new directory structure:
      <wiki_root>/concepts/, entities/, sources/, etc. (no pages/ wrapper)
      <wiki_root>/../vault/ (sibling vault directory)
    """
    root = Path(wiki_root_str).resolve()
    vault_dir = root.parent / "vault"

    # Create new-layout directories (type dirs directly under wiki root)
    (root / "concepts").mkdir(parents=True, exist_ok=True)
    (root / "entities").mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "comparisons").mkdir(parents=True, exist_ok=True)
    (root / "syntheses").mkdir(parents=True, exist_ok=True)
    (root / "indexes").mkdir(parents=True, exist_ok=True)
    (root / ".scripts").mkdir(parents=True, exist_ok=True)
    (root / ".meta").mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)

    if not (root / "CLAUDE.md").exists():
        (root / "CLAUDE.md").write_text("# Mock Schema\n", encoding="utf-8")

    # Create the vault source file referenced by generated pages
    raw_source = vault_dir / "2026-04-10-test-source.md"
    if not raw_source.exists():
        raw_source.write_text(
            "---\ntitle: Test Source\nsource_type: notes\ncaptured: 2026-04-10\n---\n\n"
            "Mock source file for generated wiki pages.\n",
            encoding="utf-8",
        )

    print(f"Generating {num_pages} mock concept pages...")
    for i in range(num_pages):
        topic = random.choice(TOPICS) + f" {i}"
        slug = topic.lower().replace(" ", "-")
        category = random.choice(CATEGORIES)
        confidence = round(random.uniform(0.5, 0.95), 2)

        content = f"""---
title: "{topic}"
type: concept
category: {category}
summary: "This is a generated summary for the fundamental understanding of {topic}."
tags: [test, generation, {category}]
aliases: [{slug.replace('-', '')}]
confidence: {confidence}
sources:
  - vault/2026-04-10-test-source.md
last_updated: 2026-04-15
stale: false
---

# {topic}

## Definition
This is a comprehensive definition of **{topic}** designed to bulk out the
token count. It focuses on the core mathematical properties and historical
lineage of the concept.

## Evidence

### From Test Source
The test source clearly highlights that {topic} is highly relevant in
scalable systems. It provides substantial evidence for the theoretical
foundations and practical applications.

## Open questions
- What are the long-term implications of {topic}?
- How does {topic} interact with emerging architectures?

## Related
- [[neural-networks-0]]
- [[machine-learning-0]]
"""
        (root / "concepts" / f"{slug}.md").write_text(content, encoding="utf-8")

    print(f"Done. Generated {num_pages} pages in {root / 'concepts'}")
    print(f"Vault: {vault_dir}")
    print(f"Run: python update_index.py {root}  (to build index files before metric/threshold)")


def calculate_metrics(wiki_root_str: str):
    """Report index token costs and page counts."""
    root = Path(wiki_root_str).resolve()
    indexes_dir = root / "indexes"

    index_files = list(indexes_dir.glob("index*.md")) if indexes_dir.is_dir() else []
    if not index_files:
        print("No index files found in indexes/. Run update_index.py first.")
        return

    total_text = ""
    file_details = []
    for idx_file in sorted(index_files):
        text = idx_file.read_text(encoding="utf-8")
        total_text += text
        words = len(text.split())
        tokens = int(words * 1.3)
        entry_count = text.count("- [[")
        file_details.append((idx_file.name, words, tokens, entry_count))

    total_words = len(total_text.split())
    total_tokens = int(total_words * 1.3)

    print("--- Index Metric Report ---")
    print()
    for name, words, tokens, entries in file_details:
        print(f"  {name:<30} {words:>6} words  ~{tokens:>6} tokens  {entries:>4} entries")
    print()
    print(f"  {'TOTAL':<30} {total_words:>6} words  ~{total_tokens:>6} tokens")
    print()
    print(f"  Estimated cost to read ALL indices (Opus @ $15/1M input): ${total_tokens / 1_000_000 * 15:.4f}")
    print(f"  Estimated cost to read ALL indices (Sonnet @ $3/1M input): ${total_tokens / 1_000_000 * 3:.4f}")
    print()

    if total_tokens >= 4000:
        print("  STATUS: SEARCH_NEEDED — semantic search would activate for queries")
    else:
        print("  STATUS: INDEX_READABLE — queries handled inline (no search overhead)")

    if total_tokens > 30000:
        print("  WARNING: Index payload is very large. Consider whether all pages need to be indexed.")


def search_latency(wiki_root_str: str, query: str):
    """Time a hybrid search query."""
    root = Path(wiki_root_str).resolve()
    script = root / ".scripts" / "hybrid_search.py"

    if not script.is_file():
        script = Path(__file__).parent / "skills" / "wiki-init" / "templates" / "scripts" / "hybrid_search.py"
        if not script.is_file():
            print("hybrid_search.py not found. Run wiki-init or provide correct path.")
            return

    print(f"Query: \"{query}\"")
    print(f"Wiki: {root}")
    print()

    # Threshold check
    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, str(script), str(root), "--check-threshold"],
        capture_output=True, text=True,
    )
    t_threshold = (time.perf_counter() - t0) * 1000
    print(f"  Threshold check: {t_threshold:.1f}ms — {result.stdout.strip()}")

    # Search
    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, str(script), str(root), query],
        capture_output=True, text=True,
    )
    t_search = (time.perf_counter() - t0) * 1000
    print(f"  Search query:    {t_search:.1f}ms")

    if result.returncode == 0:
        import json
        try:
            data = json.loads(result.stdout)
            mode = data.get("search_mode", "unknown")
            results = data.get("results", [])
            print(f"  Mode:            {mode}")
            print(f"  Results:         {len(results)}")
            if results:
                print(f"  Top result:      {results[0]['title']} (score: {results[0]['score']})")
        except json.JSONDecodeError:
            print(f"  Output: {result.stdout[:200]}")
    else:
        print(f"  Error: {result.stderr[:200]}")


def check_threshold(wiki_root_str: str):
    """Check the conditional search threshold."""
    root = Path(wiki_root_str).resolve()
    script = root / ".scripts" / "hybrid_search.py"

    if not script.is_file():
        script = Path(__file__).parent / "skills" / "wiki-init" / "templates" / "scripts" / "hybrid_search.py"

    if not script.is_file():
        print("hybrid_search.py not found.")
        return

    result = subprocess.run(
        [sys.executable, str(script), str(root), "--check-threshold"],
        capture_output=True, text=True,
    )
    parts = result.stdout.strip().split()
    if len(parts) >= 2:
        verdict = parts[0]
        tokens = int(parts[1])
        pages = int(parts[2]) if len(parts) > 2 else -1
        print(f"Verdict:   {verdict}")
        print(f"Tokens:    {tokens}")
        if pages >= 0:
            print(f"Pages:     {pages}")
        print(f"Threshold: 4000")
        if verdict == "INDEX_READABLE":
            print(f"Headroom:  {4000 - tokens} tokens before search activates")
            est_pages = (4000 - tokens) // 20  # ~20 tokens per index entry
            print(f"Estimated pages until threshold: ~{est_pages}")
    else:
        print(result.stdout)


def main():
    parser = argparse.ArgumentParser(description="Wiki Forge Benchmarker")
    subparsers = parser.add_subparsers(dest="command")

    gen_p = subparsers.add_parser("generate", help="Generate mock wiki pages")
    gen_p.add_argument("wiki_root", help="Path to wiki root")
    gen_p.add_argument("num", type=int, help="Number of pages to generate")

    met_p = subparsers.add_parser("metric", help="Report index token costs")
    met_p.add_argument("wiki_root", help="Path to wiki root")

    lat_p = subparsers.add_parser("search_latency", help="Time a search query")
    lat_p.add_argument("wiki_root", help="Path to wiki root")
    lat_p.add_argument("query", help="Query string")

    thr_p = subparsers.add_parser("threshold", help="Check search threshold")
    thr_p.add_argument("wiki_root", help="Path to wiki root")

    args = parser.parse_args()

    if args.command == "generate":
        generate_mock_wiki(args.wiki_root, args.num)
    elif args.command == "metric":
        calculate_metrics(args.wiki_root)
    elif args.command == "search_latency":
        search_latency(args.wiki_root, args.query)
    elif args.command == "threshold":
        check_threshold(args.wiki_root)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
