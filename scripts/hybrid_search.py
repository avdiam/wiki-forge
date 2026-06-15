#!/usr/bin/env python3
"""Hybrid Semantic + Keyword Search for Wiki Forge.

ONNX-based: uses onnxruntime (~50MB) + tokenizers (~5MB) instead of
sentence-transformers (~2GB). Model: all-MiniLM-L6-v2 ONNX.

Optional: zvec (~45MB) for fast approximate nearest-neighbor search on
large wikis (2000+ pages). Falls back to brute-force cosine if not installed.

Modes:
  --check-threshold    Print INDEX_READABLE or SEARCH_NEEDED + token estimate + page count
  <query>              Run hybrid BM25+semantic search, return ranked JSON
  --queries-json PATH  Read JSON array of queries from file (or '-' for stdin);
                       fuses results across all queries via RRF (Phase 2)
  --force-zvec         Force zvec ANN path (for benchmarking)
  --force-bruteforce   Force brute-force cosine path (for benchmarking)
  --rerank-typed-edges Phase 4 — typed-edge graph rerank (opt-in; off by default)

Usage:
  python hybrid_search.py <wiki-root> --check-threshold
  python hybrid_search.py <wiki-root> "query string" [--top-k 5]
  python hybrid_search.py <wiki-root> --queries-json - <<< '["q1","q2","q3"]'
  python hybrid_search.py <wiki-root> "query" --force-zvec
  python hybrid_search.py <wiki-root> "query" --force-bruteforce
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

# Heartbeat instrumentation — writes [hb] <stage> markers to stderr at each
# pipeline transition (including module-level imports). Enabled via either
# the env var `HYBRID_SEARCH_HEARTBEAT=1` (catches import-time hangs) or the
# `--heartbeat` CLI flag (catches runtime hangs after argparse). stdout (the
# JSON result) is unaffected. See dev/tech-debt.md "Root-cause hybrid_search.py
# background-execution hang" for the diagnostic plan.
_HEARTBEAT = bool(os.environ.get("HYBRID_SEARCH_HEARTBEAT"))


def _hb(stage: str) -> None:
    if _HEARTBEAT:
        print(f"[hb] {stage}", file=sys.stderr, flush=True)


_hb("module top: stdlib imports done")

# Threshold: if total index tokens are below this, the LLM can read
# the indices directly — no need for search overhead. Calibrated against
# real index entries (~60-65 tokens/entry with generous summary + tags +
# aliases); 6000 tokens ≈ ~90 pages of index before SEARCH_NEEDED trips.
INDEX_TOKEN_THRESHOLD = 6000

# Max chars of page body to include in search corpus (title + summary
# are always included in full). 2000 chars covers most Evidence sections.
MAX_BODY_CHARS = 2000

# --------------------------------------------------------------------------
# Optional imports — graceful degradation
# --------------------------------------------------------------------------

_hb("import numpy: start")
try:
    import numpy as np  # type: ignore
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
_hb(f"import numpy: done (HAS_NUMPY={HAS_NUMPY})")

# ----------------------------------------------------------------------------
# Workaround: disable platform._wmi BEFORE importing onnxruntime.
#
# Python 3.13 routes platform.system() / platform.uname() through a WMI
# (Windows Management Instrumentation) query for accurate version info via
# _wmi_query() → _wmi.exec_query() in stdlib platform.py. Under a no-TTY
# harness wrapper (e.g., Claude Code's background-task Bash mode — which
# the harness can autonomously select even when run_in_background is not
# set), the COM-based exec_query call blocks indefinitely: no output, no
# CPU progress past ~0.2 s, no timeout. onnxruntime/capi/_pybind_state.py
# line 14 calls platform.system() during import, so any `import onnxruntime`
# under the wrapper hangs forever (~30 MB RSS, 0.2 s CPU, 0 B output —
# fingerprint reproduced sessions 77 + 78).
#
# Setting platform._wmi = None makes _wmi_query() raise OSError("not
# supported") immediately, which _win32_ver() catches and falls back to
# sys.getwindowsversion() + the `ver` syscmd. Both work fine under any I/O
# configuration. platform.system() still returns "Windows" correctly; the
# only loss is the verbose service-pack detail from WMI, which neither
# onnxruntime nor this script use.
#
# Verified 2026-05-24: under the fix, `import onnxruntime` completes in
# ~0.3 s under the same harness wrapper that hung indefinitely without it.
# faulthandler.dump_traceback_later() pinpointed _wmi.exec_query() as the
# block site across three repeated dumps. See dev/tech-debt.md
# "Root-cause hybrid_search.py background-execution hang" and the
# session 78 dev/sessions.md entry for the full investigation.
# ----------------------------------------------------------------------------
import platform as _platform_mod  # noqa: PLC0415
_platform_mod._wmi = None
del _platform_mod

_hb("import onnxruntime: start")
try:
    import onnxruntime as ort  # type: ignore
    HAS_ORT = True
except ImportError:
    HAS_ORT = False
_hb(f"import onnxruntime: done (HAS_ORT={HAS_ORT})")

_hb("import tokenizers: start")
try:
    from tokenizers import Tokenizer  # type: ignore
    HAS_TOKENIZER = True
except ImportError:
    HAS_TOKENIZER = False
_hb(f"import tokenizers: done (HAS_TOKENIZER={HAS_TOKENIZER})")

_hb("import zvec: start")
try:
    import zvec  # type: ignore
    HAS_ZVEC = True
except ImportError:
    HAS_ZVEC = False
_hb(f"import zvec: done (HAS_ZVEC={HAS_ZVEC})")

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
    return wiki_root / ".meta" / "models" / "all-MiniLM-L6-v2"


def _load_model(model_dir: Path):
    """Load ONNX model + tokenizer. Cached after first call.

    Returns (None, None) if files are missing OR fail to load (e.g. truncated
    download, corrupt file, incompatible runtime). Caller degrades to
    BM25-only in either case. A stderr warning is emitted for load failures
    so the user understands why semantic search is disabled.
    """
    global _cached_tokenizer, _cached_session
    if _cached_tokenizer is not None:
        return _cached_tokenizer, _cached_session

    tokenizer_path = model_dir / "tokenizer.json"
    model_path = model_dir / "model.onnx"

    if not tokenizer_path.is_file() or not model_path.is_file():
        return None, None

    try:
        _hb("tokenizer.from_file start")
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        # Enable padding so batch encoding produces uniform lengths
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        tokenizer.enable_truncation(max_length=128)
        _hb("tokenizer.from_file done")

        _hb("ort.InferenceSession start")
        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        _hb("ort.InferenceSession done")
    except Exception as e:
        print(
            f"warning: failed to load ONNX model/tokenizer ({type(e).__name__}: {e}); "
            "falling back to BM25-only search",
            file=sys.stderr,
        )
        return None, None

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

from constants import PAGE_TYPES
from frontmatter import parse_frontmatter as _parse_frontmatter


def load_pages(wiki_root: Path) -> tuple[list[str], list[dict]]:
    """Load all wiki pages from type directories under wiki root."""
    corpus: list[str] = []
    metadata: list[dict] = []

    found_any = False
    for page_type in PAGE_TYPES:
        type_dir = wiki_root / "pages" / page_type
        if type_dir.is_dir():
            found_any = True

    if not found_any:
        return [], []

    for page_type in PAGE_TYPES:
        type_dir = wiki_root / "pages" / page_type
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

def _bm25_scalar_at_rank(rank: int) -> float:
    """Position-dependent multiplier on BM25's RRF contribution.

    BM25 is reliable on easy lexical matches at the top of the list; the
    semantic ranker earns its keep on the long tail (paraphrase / vocab
    mismatch cases). The dense list keeps its standard RRF contribution
    `1/(k+r+1)`; BM25's contribution is multiplied by this scalar, which
    starts at 3.0 (so BM25 contributes ~75% of the score for a top-ranked
    doc) and decays exponentially toward 0.667 (so BM25 contributes ~40%
    on the long tail).

    Rationale: borrowed from the qmd implementation
    (github.com/tobi/qmd). Earlier symmetric RRF (1/(k+r+1) on both lists)
    treats every rank equally, but empirically dense over-weights at the
    top of broad queries where BM25 already nailed the answer. Implemented
    as a one-sided multiplier on BM25 (rather than complementary
    list-vs-list weights) so that both lists' contributions stay
    monotonically decreasing in rank — symmetric weight schemes would let
    dense's rank-10 doc outscore its rank-0 doc.
    """
    return 0.667 + 2.333 * math.exp(-rank / 4.0)


def reciprocal_rank_fusion(
    rank_lists: list[tuple[str, list[int]]],
    k: int = 60,
    n_variations: int = 1,
) -> dict[int, float]:
    """Fuse 2N rank lists (N BM25 + N dense, or N BM25 if no dense) via RRF.

    Each list is `(kind, ranks)` where `kind` is `"bm25"` or `"dense"`.
    For each list, rank-r doc contributes:
      - BM25:  `bm25_scalar(r) / (n_variations * (k + r + 1))`
      - Dense: `1.0           / (n_variations * (k + r + 1))`

    `bm25_scalar` (Phase 1) decays from 3.0 (top) to 0.667 (tail). Division
    by `n_variations` averages contributions across paraphrase variations
    (Phase 2) so absolute scores stay comparable to Phase 1 — a doc that
    ranks 0 in both rankers for ALL N variations scores `4/(k+1)`,
    identical to the N=1 (Phase 1) cap. The skill's `0.3` score gate stays
    calibrated regardless of N.

    With N=1 and the canonical `[("bm25", sparse), ("dense", dense)]` input,
    this reduces bit-identically to Phase 1's `bm25_scalar(r)/(k+r+1) +
    1/(k+r+1)` formula.
    """
    rrf_scores: dict[int, float] = collections.defaultdict(float)
    n = max(n_variations, 1)
    for kind, ranks in rank_lists:
        for rank, doc_id in enumerate(ranks):
            denom = n * (k + rank + 1)
            if kind == "bm25":
                rrf_scores[doc_id] += _bm25_scalar_at_rank(rank) / denom
            else:  # "dense"
                rrf_scores[doc_id] += 1.0 / denom
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

    # Fallback: scan index files in pages/indexes/ directory
    indexes_dir = wiki_root / "pages" / "indexes"
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
    queries: "str | list[str]",
    top_k: int = 5,
    force_zvec: bool = False,
    force_bruteforce: bool = False,
    rerank_typed_edges: bool = False,
    rerank_top_n_seeds: int = 3,
    rerank_lambda: float = 0.05,
    rerank_window: int = 30,
) -> dict:
    """Run hybrid search. Returns JSON-serializable result dict.

    Accepts either a single query string (Phase 1 contract — N=1) or a
    list of query strings (Phase 2 — paraphrase variations). When N>1, each
    variation is run through BM25+dense and the resulting 2N rank lists
    are RRF-fused together. The first query is treated as canonical (used
    as `query` in the output JSON); the rest, if any, surface in
    `query_variations`. With N=1, behavior is bit-identical to Phase 1.

    Dense search strategy (when ONNX is available):
      - If zvec is installed and not force_bruteforce: use zvec ANN index
      - Otherwise: brute-force cosine over JSON-cached embeddings
      - force_zvec / force_bruteforce flags are for benchmarking
    """
    # Normalize input — accept str (Phase 1) or list[str] (Phase 2)
    if isinstance(queries, str):
        queries = [queries]
    if not queries:
        raise ValueError("queries must be non-empty")
    n_variations = len(queries)
    canonical_query = queries[0]
    variation_field: "list[str] | None" = (
        list(queries[1:]) if n_variations > 1 else None
    )

    _hb("load_pages start")
    corpus, metadata = load_pages(wiki_root)
    _hb(f"load_pages done: {len(corpus)} pages")
    if not corpus:
        return {
            "search_mode": "none",
            "query": canonical_query,
            "query_variations": variation_field,
            "results": [],
        }

    t_start = time.perf_counter()

    # Load stats to get inbound_links for hub dampening
    inbound_links = {}
    stats_path = wiki_root / ".meta" / "index_stats.json"
    if stats_path.is_file():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            inbound_links = stats.get("inbound_links", {})
        except Exception:
            pass

    # 1. BM25 corpus build — once, shared across all variations
    _hb("BM25 build start")
    tokenized_corpus = [_tokenize_for_bm25(doc) for doc in corpus]
    bm25 = BM25(tokenized_corpus)
    _hb("BM25 build done")

    # 2. Dense ranker setup — once, shared across all variations
    search_mode = "bm25_only"
    dense_backend = "none"
    tokenizer = None
    session = None
    use_zvec = False
    if HAS_SEMANTIC:
        model_dir = _get_model_dir(wiki_root)
        _hb("_load_model start")
        tokenizer, session = _load_model(model_dir)
        _hb("_load_model done")
        if tokenizer is not None and session is not None:
            use_zvec = HAS_ZVEC and not force_bruteforce
            if force_zvec and not HAS_ZVEC:
                use_zvec = False  # can't force what's not installed
            search_mode = "hybrid_zvec" if use_zvec else "hybrid"
            dense_backend = "zvec" if use_zvec else "bruteforce"

    has_dense = search_mode in ("hybrid", "hybrid_zvec")

    # 3. Per-variation ranking — collect 2N rank lists (N BM25 + N dense)
    rank_lists: list[tuple[str, list[int]]] = []
    bm25_scores_per_query: list[list[float]] = []
    tokenized_queries: list[list[str]] = []
    for q in queries:
        tq = _tokenize_for_bm25(q)
        tokenized_queries.append(tq)
        scores = bm25.get_scores(tq)
        bm25_scores_per_query.append(scores)
        sparse_ranks = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )
        rank_lists.append(("bm25", sparse_ranks))

        if has_dense:
            _hb(f"_embed query start (q={q[:40]!r})")
            query_emb = _embed([q], tokenizer, session)
            _hb("_embed query done")
            if use_zvec:
                dense_ranks = _zvec_search(
                    wiki_root, query_emb, corpus, metadata,
                    tokenizer, session, top_k=len(corpus),
                )
            else:
                dense_ranks = _bruteforce_search(
                    query_emb, corpus, metadata, wiki_root,
                    tokenizer, session,
                )
            rank_lists.append(("dense", dense_ranks))

    # 4. Fuse rankings
    if has_dense:
        rrf_scores = reciprocal_rank_fusion(rank_lists, n_variations=n_variations)
    elif n_variations > 1:
        # BM25-only multi-query: use RRF over N BM25 lists. Theoretical
        # max is 3.0/(k+1) — one BM25 list at rank 0, multiplier 3.0,
        # divided by N variations and summed across N lists.
        rrf_scores = reciprocal_rank_fusion(rank_lists, n_variations=n_variations)
    else:
        # BM25-only single-query (Phase 1 contract preserved): use raw
        # BM25 scores directly so IDF-sum normalization applies as before.
        rrf_scores = {i: bm25_scores_per_query[0][i] for i in range(len(corpus))}

    # Apply hub dampening and sort by score descending
    alpha = 0.05
    dampened_scores = {}
    for doc_id, score in rrf_scores.items():
        slug = metadata[doc_id]["slug"]
        links = inbound_links.get(slug, 0)
        # down-weight hubs to prevent them from dominating narrow queries
        dampened_scores[doc_id] = score / (1.0 + alpha * links)

    sorted_docs = sorted(dampened_scores.items(), key=lambda x: x[1], reverse=True)

    # Phase 4 — typed-edge graph re-rank (opt-in via rerank_typed_edges).
    # Boosts candidates that share a typed edge (contradicts / supersedes /
    # superseded_by) with any of the top-N seeds from the hybrid pass. Re-rank
    # is confined to the top-`rerank_window` candidates so a low-scored page
    # cannot be elevated into the result set just by typed-edge connection —
    # the hybrid signal stays the floor. Lambda is interpreted as a fraction
    # of the rank-0 (post-dampening) score, so the same default works under
    # hybrid (top scores ~0.06) and bm25_only (top scores ~20). Hub-dampening
    # is applied to the boost so a typed edge to a hub seed counts less.
    # Sage-wiki design ships only signal #1 (direct typed edges) at V1;
    # shared-sources / common-neighbors / type-affinity gated on supersession-
    # edge volume that the wiki doesn't yet have.
    rerank_applied = False
    if rerank_typed_edges and sorted_docs:
        from graph_analyzer import build_typed_adjacency
        typed_adj = build_typed_adjacency(wiki_root)
        n_seeds = max(1, min(rerank_top_n_seeds, len(sorted_docs)))
        seed_slugs = {metadata[doc_id]["slug"] for doc_id, _ in sorted_docs[:n_seeds]}
        rank0_raw_score = sorted_docs[0][1]
        window = min(rerank_window, len(sorted_docs))
        window_slice = sorted_docs[:window]
        rerank_count = 0
        rerank_top_window = []
        for doc_id, score in window_slice:
            slug = metadata[doc_id]["slug"]
            neighbors = typed_adj.get(slug, set())
            # Exclude self-edges — a seed connected to itself isn't a signal.
            typed_to_seeds = len((neighbors & seed_slugs) - {slug})
            if typed_to_seeds > 0:
                links = inbound_links.get(slug, 0)
                boost = (
                    rerank_lambda * rank0_raw_score * typed_to_seeds
                    / (1.0 + alpha * links)
                )
                rerank_top_window.append((doc_id, score + boost))
                rerank_count += 1
            else:
                rerank_top_window.append((doc_id, score))
        rerank_top_window.sort(key=lambda x: x[1], reverse=True)
        sorted_docs = rerank_top_window + sorted_docs[window:]
        rerank_applied = rerank_count > 0

    # Compute normalization. The "perfect-match" cap depends on which ranker
    # combination is in play (see branches below), but in all cases the cap
    # is the raw-RRF ceiling for a doc that's rank-0 across the active
    # rankers. Hub dampening pushes every doc's score down by
    # 1/(1 + alpha*links), so a cap that ignores dampening yields normalized
    # scores that underread the actual match quality — the rank-0 doc, by
    # construction the best possible match for *this* query, would never
    # reach 1.0 if it happened to be a hub. Fix: scale the cap by the
    # rank-0 doc's dampening factor, so "1.0 = the rank-0 doc's quality
    # interpreted as raw_rrf / raw_rrf_max." Preserves the SKILL.md text
    # ("a score of 0.3 means the best result is only 30% as relevant as a
    # perfect match") as a literal claim rather than an approximate one.
    bm25_top_raw = sorted_docs[0][1] if sorted_docs and sorted_docs[0][1] > 0 else 0.0
    rank0_doc_id = sorted_docs[0][0] if sorted_docs else None
    rank0_links = (
        inbound_links.get(metadata[rank0_doc_id]["slug"], 0)
        if rank0_doc_id is not None else 0
    )
    rank0_dampening = 1.0 + alpha * rank0_links  # same alpha as line 671
    if has_dense:
        # Hybrid (Phase 1 + Phase 2): a doc that's rank-0 in both BM25
        # and dense for ALL N variations sums to 4.0/(k+1). The /N in the
        # RRF formula keeps this cap stable regardless of N.
        theoretical_max = 4.0 / (60 + 1) / rank0_dampening
    elif n_variations > 1:
        # BM25-only multi-query: cap is 3.0/(k+1) (BM25 multiplier alone).
        theoretical_max = 3.0 / (60 + 1) / rank0_dampening
    else:
        # BM25-only single-query (Phase 1 contract): IDF-sum normalization.
        theoretical_max = sum(
            bm25.idf.get(w, 0.0) * (bm25.k1 + 1) for w in tokenized_queries[0]
        )
        if theoretical_max <= 0:
            theoretical_max = 1.0
        theoretical_max = theoretical_max / rank0_dampening

    t_total = time.perf_counter() - t_start

    # Snippet uses the canonical query's tokens for highlighting
    canonical_tokens = tokenized_queries[0]

    results = []
    for doc_id, score in sorted_docs[:top_k]:
        meta = metadata[doc_id]
        normalized = min(score / theoretical_max, 1.0)
        snippet = _extract_snippet(corpus[doc_id], canonical_tokens)
        results.append({
            "path": meta["path"],
            "title": meta["title"],
            "slug": meta["slug"],
            "summary": meta["summary"],
            "score": round(normalized, 3),
            "snippet": snippet,
        })

    # Append `_graph_rerank` suffix to search_mode when the typed-edge rerank
    # actually moved at least one candidate. The flag-set-but-no-boost case
    # keeps the base search_mode so telemetry distinguishes "rerank was off"
    # from "rerank was on but had nothing to do" from "rerank reshuffled".
    output_search_mode = search_mode
    if rerank_typed_edges:
        output_search_mode = (
            f"{search_mode}_graph_rerank" if rerank_applied else f"{search_mode}_graph_rerank_noop"
        )

    output = {
        "search_mode": output_search_mode,
        "dense_backend": dense_backend,
        "query": canonical_query,
        "query_variations": variation_field,
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
        "--heartbeat",
        action="store_true",
        help=(
            "Emit [hb] <stage> markers to stderr at each pipeline transition. "
            "For diagnosing hangs under the no-TTY harness wrapper. "
            "stdout (the JSON result) is unaffected."
        ),
    )
    parser.add_argument(
        "--check-threshold",
        action="store_true",
        help="Check if index is small enough for direct LLM reading",
    )
    parser.add_argument(
        "--queries-json",
        default=None,
        help=(
            "Read JSON array of queries from file (or '-' for stdin). "
            "When N>1, runs BM25+dense per query and RRF-fuses all 2N rank lists."
        ),
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
    parser.add_argument(
        "--rerank-typed-edges",
        action="store_true",
        help=(
            "Phase 4 — re-rank the top-N hybrid candidates by typed-edge "
            "proximity to the top-K seed pages. Off by default; opt-in until "
            "the eval harness justifies flipping the default."
        ),
    )
    parser.add_argument(
        "--rerank-top-n-seeds",
        type=int,
        default=3,
        help="Phase 4 — number of top hybrid results treated as seeds (default 3)",
    )
    parser.add_argument(
        "--rerank-lambda",
        type=float,
        default=0.05,
        help=(
            "Phase 4 — typed-edge boost as a fraction of rank-0 score per "
            "edge-to-seed (default 0.05 = 'one typed edge ≈ 5%% of rank-0 "
            "quality'). Scale-relative so the default works under both "
            "hybrid and bm25_only modes. Hub-dampened with the same alpha "
            "as the hybrid score so edges to high-inbound seeds count less."
        ),
    )
    parser.add_argument(
        "--rerank-window",
        type=int,
        default=30,
        help=(
            "Phase 4 — re-rank window. Only the top-N hybrid candidates are "
            "eligible for re-ordering (default 30). Pages outside the window "
            "cannot be promoted into the result set by typed edges alone."
        ),
    )
    args = parser.parse_args()

    if args.heartbeat:
        global _HEARTBEAT
        _HEARTBEAT = True
        _hb("main entered (argparse done)")

    wiki_root = Path(args.wiki_root).resolve()

    if args.check_threshold:
        if not _validate_wiki_root(wiki_root):
            print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
            return 2
        verdict, tokens, pages = check_threshold(wiki_root)
        print(f"{verdict} {tokens} {pages}")
        return 0

    # Resolve queries from either positional arg or --queries-json
    if args.queries_json is not None:
        try:
            if args.queries_json == "-":
                raw = sys.stdin.read()
            else:
                raw = Path(args.queries_json).read_text(encoding="utf-8")
            queries = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            print(f"error: failed to read --queries-json: {e}", file=sys.stderr)
            return 2
        if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
            print("error: --queries-json must contain a JSON array of strings", file=sys.stderr)
            return 2
        if not queries:
            print("error: --queries-json array is empty", file=sys.stderr)
            return 2
    elif args.query:
        queries = [args.query]
    else:
        print(
            "error: provide a query string, --queries-json, or --check-threshold",
            file=sys.stderr,
        )
        return 2

    if not _validate_wiki_root(wiki_root):
        print(f"error: {wiki_root} does not look like a wiki root (no CLAUDE.md)", file=sys.stderr)
        return 2

    result = search(
        wiki_root, queries, args.top_k,
        force_zvec=args.force_zvec,
        force_bruteforce=args.force_bruteforce,
        rerank_typed_edges=args.rerank_typed_edges,
        rerank_top_n_seeds=args.rerank_top_n_seeds,
        rerank_lambda=args.rerank_lambda,
        rerank_window=args.rerank_window,
    )
    if result["search_mode"] == "bm25_only":
        if len(queries) > 1:
            # Phase 2 with no semantic ranker: variations widen recall over
            # BM25 alone, but the bigger win comes from pairing variations
            # with semantic search. Sharpen the upgrade nudge here.
            result["note"] = (
                "Running in BM25-only mode with multi-query expansion (keyword "
                "matching across paraphrases). Semantic search would compose "
                "with the variations for a larger recall lift on vague queries. "
                "Install: python .scripts/setup_search.py"
            )
        else:
            result["note"] = (
                "Running in BM25-only mode (keyword matching). "
                "For semantic search, run: python .scripts/setup_search.py"
            )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
