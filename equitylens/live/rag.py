"""Local semantic + BM25 retrieval. Company passages and vectors live only in RAM."""

import math
import os
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass

import numpy as np

STOP = set(
    "a an the and or of to in on for with by is are was were be been it its this that these those what which how why when where who tell me about please can could would should does do did company".split()
)


def tokens(text):
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1 and token not in STOP]


@dataclass
class Passage:
    id: str
    source_id: str
    title: str
    text: str
    url: str
    locator: str
    evidence_level: str
    category: str
    publisher: str
    published_at: str | None
    retrieved_at: str


def chunk_blocks(source, blocks, *, level=None, max_chunks=35):
    result = []
    for block in blocks:
        text = " ".join(block["text"].split())
        for start in range(0, len(text), 600):
            part = text[start : start + 750]
            minimum = 8 if source.evidence_level == "headline" else 30
            if len(part.strip()) < minimum:
                continue
            result.append(
                Passage(
                    id=f"{source.id}:C{len(result) + 1:03}",
                    source_id=source.id,
                    title=source.title,
                    text=part,
                    url=source.url,
                    locator=f"{block['locator']}, extracted chars {start}–{start + len(part)}",
                    evidence_level=level or source.evidence_level,
                    category=source.category,
                    publisher=source.publisher,
                    published_at=source.published_at.isoformat() if source.published_at else None,
                    retrieved_at=source.retrieved_at.isoformat(),
                )
            )
            if len(result) >= max_chunks:
                return result
    return result


class LocalEmbedder:
    def __init__(self, settings):
        self.model_name = settings.rag_embedding_model
        self.cache_dir = str(settings.rag_model_cache)
        self.download_if_missing = settings.rag_download_model_if_missing
        self._model = None
        self._lock = threading.RLock()

    def _load(self):
        if self._model is None:
            os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
            os.environ["TOKENIZERS_PARALLELISM"] = "false"
            from fastembed import TextEmbedding

            # Setup downloads weights explicitly; research requests never download a model.
            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=self.cache_dir,
                threads=2,
                local_files_only=not self.download_if_missing,
            )
        return self._model

    def documents(self, texts):
        with self._lock:
            vectors = list(self._load().passage_embed(texts, batch_size=32))
        return np.asarray(vectors, dtype=np.float32)

    def query(self, text):
        with self._lock:
            vector = next(iter(self._load().query_embed(text)))
        return np.asarray(vector, dtype=np.float32)


def normalized(vectors):
    vectors = np.asarray(vectors, dtype=np.float32)
    if not np.isfinite(vectors).all():
        raise ValueError("Invalid embedding values.")
    lengths = np.linalg.norm(vectors, axis=-1, keepdims=True)
    if np.any(lengths == 0):
        raise ValueError("Empty embedding vector.")
    return vectors / lengths


class SessionIndex:
    def __init__(self, passages, embedder, ignored_tokens=()):
        self.passages = list(passages[:250])
        self.embedder = embedder
        self.ignored_tokens = set(ignored_tokens)
        self._lock = threading.RLock()
        self.vectors = None
        self.notice = ""
        self._counts = [Counter(tokens(p.title + " " + p.text)) for p in self.passages]
        self._lengths = [sum(c.values()) for c in self._counts]
        self._frequency = Counter(token for row in self._counts for token in row)
        if self.passages:
            try:
                texts = [p.title + "\n" + p.text for p in self.passages]
                vectors = embedder.documents(texts)
                if len(vectors) != len(self.passages) or vectors.ndim != 2:
                    raise ValueError("Embedding dimensions do not match passages.")
                self.vectors = normalized(vectors)
            except (OSError, RuntimeError, ValueError, ImportError):
                self.notice = (
                    "Local embedding model unavailable. Retrieval is lexical-only until model setup succeeds."
                )

    def summary(self):
        return {
            "mode": "hybrid_semantic_bm25" if self.vectors is not None else "bm25_only",
            "model": self.embedder.model_name if self.vectors is not None else None,
            "passages": len(self.passages),
            "sources": len({p.source_id for p in self.passages}),
            "dimensions": int(self.vectors.shape[1]) if self.vectors is not None else 0,
            "storage": "session memory only",
            "notice": self.notice,
        }

    def _bm25(self, query):
        terms = set(tokens(query)) - self.ignored_tokens
        n = len(self.passages)
        avg = sum(self._lengths) / n if n else 1
        scores = np.zeros(n)
        for i, row in enumerate(self._counts):
            for term in terms:
                count = row[term]
                if count:
                    frequency = self._frequency[term]
                    idf = math.log(1 + (n - frequency + 0.5) / (frequency + 0.5))
                    scores[i] += (
                        idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * self._lengths[i] / max(avg, 1)))
                    )
        return scores

    def search(self, query, *, top_k=6, max_chars=6500):
        with self._lock:
            if not self.passages:
                return []
            lexical = self._bm25(query)
            semantic = np.zeros(len(self.passages))
            has_semantic = self.vectors is not None
            if has_semantic:
                try:
                    vector = normalized(self.embedder.query(query))
                    semantic = self.vectors @ vector
                except (OSError, RuntimeError, ValueError):
                    has_semantic = False
            candidates = [
                i
                for i in range(len(self.passages))
                if lexical[i] > 0 or (has_semantic and semantic[i] >= 0.45)
            ]
            if not candidates:
                return []
            fused = np.zeros(len(self.passages))
            for ordering, weight in [
                (
                    sorted((i for i in candidates if lexical[i] > 0), key=lambda i: lexical[i], reverse=True),
                    1,
                ),
                (
                    sorted(
                        (i for i in candidates if semantic[i] >= 0.45),
                        key=lambda i: semantic[i],
                        reverse=True,
                    ),
                    1 if has_semantic else 0,
                ),
            ]:
                for rank, index in enumerate(ordering, 1):
                    fused[index] += weight / (60 + rank)
            if fused.max() > 0:
                fused /= fused.max()
            selected, used_sources, used_chars = [], Counter(), 0
            while candidates and len(selected) < top_k:

                def mmr(index):
                    redundancy = (
                        max(
                            (float(self.vectors[index] @ self.vectors[previous]) for previous in selected),
                            default=0,
                        )
                        if has_semantic
                        else 0
                    )
                    return 0.8 * fused[index] - 0.2 * redundancy

                candidate = max(candidates, key=mmr)
                candidates.remove(candidate)
                passage = self.passages[candidate]
                if used_sources[passage.source_id] >= 2 or used_chars + len(passage.text) > max_chars:
                    continue
                selected.append(candidate)
                used_sources[passage.source_id] += 1
                used_chars += len(passage.text)
            return [
                {
                    **asdict(self.passages[i]),
                    "semantic_score": round(float(semantic[i]), 4) if has_semantic else None,
                    "keyword_score": round(float(lexical[i]), 4),
                    "rank_score": round(float(fused[i]), 4),
                }
                for i in selected
            ]

    def report_context(self):
        queries = [
            "stock price trading volume valuation P/E EPS price performance volatility",
            "financial performance revenue profit cash flow debt",
            "new projects investments expansion and business developments",
            "industry demand competition regulation and market outlook",
            "material risks and conditional future opportunities",
        ]
        selected, seen = [], set()
        for query in queries:
            for passage in self.search(query, top_k=3, max_chars=2400):
                if (
                    passage["id"] not in seen
                    and sum(len(p["text"]) for p in selected) + len(passage["text"]) <= 8500
                ):
                    selected.append(passage)
                    seen.add(passage["id"])
        return selected[:12]

    def clear(self):
        with self._lock:
            if self.vectors is not None:
                self.vectors.fill(0)
            self.vectors = None
            self.passages.clear()
            self._counts.clear()
            self._lengths.clear()
            self._frequency.clear()
            self.ignored_tokens.clear()
