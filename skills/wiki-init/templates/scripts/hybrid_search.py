#!/usr/bin/env python3
"""Hybrid Semantic + Keyword Search for Wiki Forge.

ONNX-based: uses onnxruntime (~50MB) + tokenizers (~5MB) instead of
sentence-transformers (~2GB). Model: all-MiniLM-L6-v2 ONNX.

Optional: zvec (~45MB) for fast approximate nearest-neighbor search on
large wikis (2000+ pages). Falls back to brute-force cosine if not installed.

Modes:
  --check-threshold    Print INDEX_READABLE or SEARCH_NEEDED + token estimate + page count
  <query>              Run hybrid BM25+semantic search, return ranked JSON
  --force-zvec         Force zvec ANN path (for benchmarking)
  --force-bruteforce   Force brute-force cosine path (for benchmarking)

Usage:
  python hybrid_search.py <wiki-root> --check-threshold
  python hybrid_search.py <wiki-root> "query string" [--top-k 5]
  python hybrid_search.py <wiki-root> "query" --force-zvec
  python hybrid_search.py <wiki-root> "query" --force-bruteforce
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import sys
import time
from pathlib import Path

# Threshold: if total index tokens are below this, the LLM can read
# the indices directly — no need for search overhead.
INDEX_TOKEN_THRESHOLD = 4000

# Max chars of page body to include in search corpus (title + summary
# are always included in full). 2000 chars covers most Evidence sections.
MAX_BODY_CHARS = 2000

# --------------------------------------------------------------------------
# Optional imports — graceful degradation
# --------------------------------------------------------------------------

try:
    import numpy as np  # type: ignore
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import onnxruntime as ort  # type: ignore
    HAS_ORT = True
except ImportError:
    HAS_ORT = False

try:
    from tokenizers import Tokenizer  # type: ignore
    HAS_TOKENIZER = True
except ImportError:
    HAS_TOKENIZER = False

try:
    import zvec  # type: ignore
    HAS_ZVEC = True
except ImportError:
    HAS_ZVEC = False

HAS_SEMANTIC = HAS_NUMPY and HAS_ORT and HAS_TOKENIZER

# Regex to strip punctuation for BM25 tokenization
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Common English stopwords — filtered from BM25 queries and corpus to
# prevent high-frequency function words from diluting relevance scores
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to",
    "of", "in", "on", "at", "for", "by", "with", "from", "is", "are",
    "was", "were", "be", "been", "being", "this", "that", "these",
    "those", "it", "its", "as", "we", "you", "they", "their", "our",
    "us", "not", "no", "do", "does", "did", "will", "would", "can",
    "could", "should", "about", "into", "than", "out", "up", "down",
    "over", "under", "also", "what", "how", "when", "where", "which",
}


# --------------------------------------------------------------------------
# BM25 — pure Python, no deps (kept from fork)
# --------------------------------------------------------------------------

class BM25:
    """Okapi BM25 ranking function. Operates on pre-tokenized documents."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.corpus_size = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / self.corpus_size if self.corpus_size else 1
        self.corpus = corpus
        self.k1 = k1
        self.b = b

        self.df: dict[str, int] = collections.defaultdict(int)
        self.doc_freqs: list[collections.Counter] = []
        for doc in corpus:
            freqs = collections.Counter(doc)
            self.doc_freqs.append(freqs)
            for word in freqs:
                self.df[word] += 1

        self.idf: dict[str, float] = {}
        for word, freq in self.df.items():
            self.idf[word] = math.log(
                ((self.corpus_size - freq + 0.5) / (freq + 0.5)) + 1
            )

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        for word in query:
            if word not in self.df:
                continue
            q_idf = self.idf[word]
            for i, doc_freqs in enumerate(self.doc_freqs):
                if word in doc_freqs:
                    freq = doc_freqs[word]
                    doc_len = len(self.corpus[i])
                    denom = freq + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                    if denom == 0:
                        continue
                    score = q_idf * (freq * (self.k1 + 1)) / denom
                    scores[i] += score
        return scores


def _tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize text for BM25: lowercase, strip punctuation, remove stopwords."""
    cleaned = _PUNCT_RE.sub(" ", text.lower())
    return [w for w in cleaned.split() if len(w) > 1 and w not in STOPWORDS]


# --------------------------------------------------------------------------
# ONNX embedding
# --------------------------------------------------------------------------

# Module-level cache for model + tokenizer
_cached_tokenizer = None
_cached_session = None


def _get_model_dir(wiki_root: Path) -> Path:
    return wiki_root / ".scripts" / "models" / "all-MiniLM-L6-v2"


def _load_model(model_dir: Path):
    """Load ONNX model + tokenizer. Cached after first call."""
    global _cached_tokenizer, _cached_session
    if _cached_tokenizer is not None:
        return _cached_tokenizer, _cached_session

    tokenizer_path = model_dir / "tokenizer.json"
    model_path = model_dir / "model.onnx"

    if not tokenizer_path.is_file() or not model_path.is_file():
        return None, None

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    # Enable padding so batch encoding produces uniform lengths
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    tokenizer.enable_truncation(max_length=128)

    session = ort.InferenceSession(
        str(model_path),
        providers=["CPUExecutionProvider"],
    )

    _cached_tokenizer = tokenizer
    _cached_session = session
    return tokenizer, session


def _embed(texts: list[str], tokenizer, session) -> "np.ndarray":
    """Encode texts to L2-normalized embeddings using ONNX session."""
    encoded = tokenizer.encode_batch(texts)

    input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
    token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

    outputs = session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )

    # outputs[0] is token embeddings: (batch, seq_len, hidden_dim)
    token_embeddings = outputs[0]

    # Mean pooling with attention mask
    mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
    sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
    sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
    mean_embeddings = sum_embeddings / sum_mask

    # L2 normalize
    norms = np.linalg.norm(mean_embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, a_min=1e-9, a_max=None)
    normalized = mean_embeddings / norms

    return normalized


def _cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> "np.ndarray":
    """Compute cosine similarity between a single vector and a matrix."""
    # a: (1, dim), b: (N, dim) — both already L2-normalized
    return np.dot(b, a.T).flatten()


# --------------------------------------------------------------------------
# Embedding cache (JSON — brute-force fallback)
# --------------------------------------------------------------------------

def _load_cache(wiki_root: Path) -> dict:
    cache_path = wiki_root / ".meta" / "embeddings_cache.json"
    if cache_path.is_file():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(wiki_root: Path, cache: dict, current_paths: set[str]) -> None:
    """Save cache, pruning entries for pages that no longer exist."""
    pruned = {k: v for k, v in cache.items() if k in current_paths}
    meta_dir = wiki_root / ".meta"
    meta_dir.mkdir(exist_ok=True)
    cache_path = meta_dir / "embeddings_cache.json"
    cache_path.write_text(json.dumps(pruned), encoding="utf-8")


# --------------------------------------------------------------------------
# zvec vector index (optional — fast ANN for large wikis)
# --------------------------------------------------------------------------

def _get_zvec_path(wiki_root: Path) -> Path:
    return wiki_root / ".meta" / "vectors.zvec"


def _zvec_search(
    wiki_root: Path,
    query_embedding: "np.ndarray",
    corpus: list[str],
    metadata: list[dict],
    tokenizer,
    session,
    top_k: int,
) -> list[int]:
    """Use zvec for ANN search. Rebuilds index from cached embeddings.

    Embedding computation (the expensive part) uses the mtime cache shared
    with the brute-force path. The zvec index itself is rebuilt each query
    from pre-computed embeddings — this is fast (sub-ms for 2000 pages)
    and avoids sync issues with zvec's persistent state.

    Returns ranked list of doc indices (best first).
    """
    import shutil

    zvec_path = _get_zvec_path(wiki_root)
    dim = query_embedding.shape[1]

    current_paths = {m["path"] for m in metadata}
    cache = _load_cache(wiki_root)
    cache_updated = False
    embeddings: list[list[float]] = []

    for i, meta in enumerate(metadata):
        page_path = meta["path"]
        cached = cache.get(page_path)
        if cached and abs(cached.get("mtime", 0) - meta["mtime"]) < 0.01:
            embeddings.append(cached["embedding"])
        else:
            emb = _embed([corpus[i]], tokenizer, session)[0].tolist()
            cache[page_path] = {"mtime": meta["mtime"], "embedding": emb}
            cache_updated = True
            embeddings.append(emb)

    if cache_updated:
        _save_cache(wiki_root, cache, current_paths)

    if zvec_path.exists():
        shutil.rmtree(zvec_path, ignore_errors=True)

    schema = zvec.CollectionSchema(
        name="wiki_vectors",
        vectors=zvec.VectorSchema("embedding", zvec.DataType.VECTOR_FP32, dim),
    )
    db = zvec.create_and_open(path=str(zvec_path), schema=schema)

    docs = [
        zvec.Doc(id=meta["path"], vectors={"embedding": embeddings[i]})
        for i, meta in enumerate(metadata)
    ]
    if docs:
        db.insert(docs)

    results = db.query(
        zvec.VectorQuery("embedding", vector=query_embedding[0].tolist()),
        topk=top_k,
    )

    ranked_indices = []
    path_to_idx = {m["path"]: i for i, m in enumerate(metadata)}
    for result in results:
        rid = result.get("id", "") if isinstance(result, dict) else getattr(result, "id", "")
        if rid in path_to_idx:
            ranked_indices.append(path_to_idx[rid])

    return ranked_indices


def _bruteforce_search(
    query_embedding: "np.ndarray",
    corpus: list[str],
    metadata: list[dict],
    wiki_root: Path,
    tokenizer,
    session,
) -> list[int]:
    """Brute-force cosine similarity over cached embeddings.

    Returns ranked list of doc indices (best first).
    """
    current_paths = {m["path"] for m in metadata}
    cache = _load_cache(wiki_root)
    corpus_embeddings = []
    cache_updated = False

    for i, meta in enumerate(metadata):
        cache_key = meta["path"]
        cached = cache.get(cache_key)
        if cached and abs(cached.get("mtime", 0) - meta["mtime"]) < 0.01:
            corpus_embeddings.append(cached["embedding"])
        else:
            emb = _embed([corpus[i]], tokenizer, session)[0].tolist()
            corpus_embeddings.append(emb)
            cache[cache_key] = {"mtime": meta["mtime"], "embedding": emb}
            cache_updated = True

    if cache_updated:
        _save_cache(wiki_root, cache, current_paths)

    corpus_emb_np = np.array(corpus_embeddings, dtype=np.float32)
    similarities = _cosine_similarity(query_embedding, corpus_emb_np)
    return sorted(range(len(similarities)), key=lambda i: similarities[i], reverse=True)


# --------------------------------------------------------------------------
# Page loading
# --------------------------------------------------------------------------

from frontmatter import parse_frontmatter as _parse_frontmatter


PAGE_TYPES = ["sources", "concepts", "entities", "comparisons", "syntheses"]


def load_pages(wiki_root: Path) -> tuple[list[str], list[dict]]:
    """Load all wiki pages from type directories under wiki root."""
    corpus: list[str] = []
    metadata: list[dict] = []

    found_any = False
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if type_dir.is_dir():
            found_any = True

    if not found_any:
        return [], []

    for page_type in PAGE_TYPES:
        type_dir = wiki_root / page_type
        if not type_dir.is_dir():
            continue
        for md_file in sorted(type_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            fm = _parse_frontmatter(text)

            title = fm.get("title") or md_file.stem
            summary = fm.get("summary") or ""
            tags = fm.get("tags") or []
            aliases = fm.get("aliases") or []
            tag_str = " ".join(str(t) for t in tags if t)
            alias_str = " ".join(str(a) for a in aliases if a)
            doc_text = f"{title}. {summary}. {tag_str} {alias_str} {text[:MAX_BODY_CHARS]}".replace("\n", " ")

            corpus.append(doc_text)
            metadata.append({
                "path": md_file.relative_to(wiki_root).as_posix(),
                "title": title,
                "slug": md_file.stem,
                "summary": summary,
                "mtime": md_file.stat().st_mtime,
            })
    return corpus, metadata


# --------------------------------------------------------------------------
# Snippet extraction
# --------------------------------------------------------------------------

def _extract_snippet(text: str, query_tokens: list[str], width: int = 220) -> str:
    """Extract a text snippet around the first matching query term."""
    lower = text.lower()
    for term in query_tokens:
        idx = lower.find(term)
        if idx >= 0:
            start = max(0, idx - width // 3)
            end = min(len(text), start + width)
            s = text[start:end].replace("\n", " ")
            return ("..." if start > 0 else "") + s + ("..." if end < len(text) else "")
    return text[:width].replace("\n", " ") + ("..." if len(text) > width else "")


# --------------------------------------------------------------------------
# Reciprocal Rank Fusion
# --------------------------------------------------------------------------

def reciprocal_rank_fusion(
    dense_ranks: list[int], sparse_ranks: list[int], k: int = 60
) -> dict[int, float]:
    """Fuse dense and sparse rankings via RRF. Returns {doc_id: score}."""
    rrf_scores: dict[int, float] = collections.defaultdict(float)
    for rank, doc_id in enumerate(dense_ranks):
        rrf_scores[doc_id] += 1.0 / (k + rank + 1)
    for rank, doc_id in enumerate(sparse_ranks):
        rrf_scores[doc_id] += 1.0 / (k + rank + 1)
    return dict(rrf_scores)


# --------------------------------------------------------------------------
# Threshold check
# --------------------------------------------------------------------------

def check_threshold(wiki_root: Path) -> tuple[str, int, int]:
    """Check if index is small enough for direct LLM reading.

    Returns ("INDEX_READABLE"|"SEARCH_NEEDED", tokens, page_count).
    """
    # Try the cached stats first
    stats_path = wiki_root / ".meta" / "index_stats.json"
    if stats_path.is_file():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            tokens = stats.get("estimated_tokens", 0)
            pages = stats.get("total_pages", 0)
            verdict = "INDEX_READABLE" if tokens < INDEX_TOKEN_THRESHOLD else "SEARCH_NEEDED"
            return verdict, tokens, pages
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: scan index files in indexes/ directory
    indexes_dir = wiki_root / "indexes"
    total_words = 0
    if indexes_dir.is_dir():
        for idx_file in indexes_dir.glob("index*.md"):
            try:
                total_words += len(idx_file.read_text(encoding="utf-8").split())
            except OSError:
                continue

    tokens = int(total_words * 1.3)
    verdict = "INDEX_READABLE" if tokens < INDEX_TOKEN_THRESHOLD else "SEARCH_NEEDED"
    return verdict, tokens, -1  # -1 = page count unknown without stats


# --------------------------------------------------------------------------
# Main search
# --------------------------------------------------------------------------

def search(
    wiki_root: Path,
    query: str,
    top_k: int = 5,
    force_zvec: bool = False,
    force_bruteforce: bool = False,
) -> dict:
    """Run hybrid search. Returns JSON-serializable result dict.

    Dense search strategy (when ONNX is available):
      - If zvec is installed and not force_bruteforce: use zvec ANN index
      - Otherwise: brute-force cosine over JSON-cached embeddings
      - force_zvec / force_bruteforce flags are for benchmarking
    """
    corpus, metadata = load_pages(wiki_root)
    if not corpus:
        return {"search_mode": "none", "query": query, "results": []}

    t_start = time.perf_counter()

    # 1. Sparse Search (BM25) with stopword removal
    tokenized_corpus = [_tokenize_for_bm25(doc) for doc in corpus]
    tokenized_query = _tokenize_for_bm25(query)
    bm25 = BM25(tokenized_corpus)
    bm25_scores = bm25.get_scores(tokenized_query)
    sparse_ranks = sorted(
        range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True
    )

    # 2. Dense Search (ONNX semantic) — if available
    search_mode = "bm25_only"
    dense_backend = "none"
    dense_ranks = list(range(len(corpus)))  # default: original order

    if HAS_SEMANTIC:
        model_dir = _get_model_dir(wiki_root)
        tokenizer, session = _load_model(model_dir)
        if tokenizer is not None and session is not None:
            query_emb = _embed([query], tokenizer, session)

            # Choose dense search backend
            use_zvec = HAS_ZVEC and not force_bruteforce
            if force_zvec and not HAS_ZVEC:
                use_zvec = False  # can't force what's not installed

            if use_zvec:
                search_mode = "hybrid_zvec"
                dense_backend = "zvec"
                dense_ranks = _zvec_search(
                    wiki_root, query_emb, corpus, metadata,
                    tokenizer, session, top_k=len(corpus),
                )
            else:
                search_mode = "hybrid"
                dense_backend = "bruteforce"
                dense_ranks = _bruteforce_search(
                    query_emb, corpus, metadata, wiki_root,
                    tokenizer, session,
                )

    t_dense = time.perf_counter()

    # 3. Fuse rankings
    if search_mode in ("hybrid", "hybrid_zvec"):
        rrf_scores = reciprocal_rank_fusion(dense_ranks, sparse_ranks)
    else:
        # BM25-only: use BM25 scores directly for ranking
        rrf_scores = {i: bm25_scores[i] for i in range(len(bm25_scores))}

    # Sort by score descending
    sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

    # Compute scores:
    # - For hybrid mode (RRF): normalize against theoretical max
    # - For BM25-only: normalize against theoretical BM25 max (sum of
    #   query-term IDFs) so low-quality matches get low normalized scores
    bm25_top_raw = sorted_docs[0][1] if sorted_docs and sorted_docs[0][1] > 0 else 0.0
    if search_mode in ("hybrid", "hybrid_zvec"):
        theoretical_max = 2.0 / (60 + 1)  # k=60 → 0.03279...
    else:
        theoretical_max = sum(
            bm25.idf.get(w, 0.0) * (bm25.k1 + 1) for w in tokenized_query
        )
        if theoretical_max <= 0:
            theoretical_max = 1.0

    t_total = time.perf_counter() - t_start

    results = []
    for doc_id, score in sorted_docs[:top_k]:
        meta = metadata[doc_id]
        normalized = min(score / theoretical_max, 1.0)
        snippet = _extract_snippet(corpus[doc_id], tokenized_query)
        results.append({
            "path": meta["path"],
            "title": meta["title"],
            "slug": meta["slug"],
            "summary": meta["summary"],
            "score": round(normalized, 3),
            "snippet": snippet,
        })

    output = {
        "search_mode": search_mode,
        "dense_backend": dense_backend,
        "query": query,
        "corpus_size": len(corpus),
        "search_time_ms": round(t_total * 1000, 1),
        "results": results,
    }
    if search_mode == "bm25_only":
        output["bm25_top_raw_score"] = round(bm25_top_raw, 3)
    return output


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _validate_wiki_root(wiki_root: Path) -> bool:
    """Check that the path looks like a wiki root."""
    return (wiki_root / "CLAUDE.md").is_file()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hybrid semantic + keyword search for Wiki Forge"
    )
    parser.add_argument("wiki_root", help="Path to the wiki root")
    parser.add_argument("query", nargs="?", default=None, help="Search query")
    parser.add_argument(
        "--check-threshold",
        action="store_true",
        help="Check if index is small enough for direct LLM reading",
    )
    parser.add_argument(
        "--top-k", type=int, default=5, help="Number of results to return (default: 5)"
    )
    parser.add_argument(
        "--force-zvec",
        action="store_true",
        help="Force zvec ANN backend (for benchmarking)",
    )
    parser.add_argument(
        "--force-bruteforce",
        action="store_true",
        help="Force brute-force cosine backend (for benchmarking)",
    )
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root).resolve()

    if args.check_threshold:
        if not _validate_wiki_root(wiki_root):
            print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
            return 2
        verdict, tokens, pages = check_threshold(wiki_root)
        print(f"{verdict} {tokens} {pages}")
        return 0

    if not args.query:
        print("error: provide a query string or use --check-threshold", file=sys.stderr)
        return 2

    if not _validate_wiki_root(wiki_root):
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    result = search(
        wiki_root, args.query, args.top_k,
        force_zvec=args.force_zvec,
        force_bruteforce=args.force_bruteforce,
    )
    if result["search_mode"] == "bm25_only":
        result["note"] = (
            "Running in BM25-only mode (keyword matching). "
            "For semantic search, run: python .scripts/setup_search.py"
        )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
