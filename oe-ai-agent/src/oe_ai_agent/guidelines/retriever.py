"""Hybrid keyword/vector retrieval over the clinical guideline corpus."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from oe_ai_agent.guidelines.bm25 import Bm25Index, RankedChunk, tokenize
from oe_ai_agent.guidelines.cohere import GuidelineProviderError, GuidelineRetrievalProvider
from oe_ai_agent.guidelines.corpus import (
    chunk_guideline_documents,
    load_guideline_documents,
)
from oe_ai_agent.guidelines.models import GuidelineChunk
from oe_ai_agent.guidelines.store import GuidelineIndexStore

DEFAULT_LIMIT = 6
MAX_LIMIT = 10
MIN_SNIPPET_TERM_LENGTH = 3
RRF_K = 60.0


@dataclass(frozen=True)
class GuidelineSearchResult:
    chunk: GuidelineChunk
    score: float
    snippet: str
    retrieval_method: str


@dataclass(frozen=True)
class GuidelineSearchResponse:
    results: list[GuidelineSearchResult]
    warnings: list[str]


class ClinicalGuidelineRetriever:
    def __init__(
        self,
        *,
        corpus_dir: Path,
        index_path: Path,
        provider: GuidelineRetrievalProvider | None,
        embed_model: str,
        embed_dimension: int,
        keyword_top_k: int = 25,
        dense_top_k: int = 25,
        rerank_candidate_limit: int = 40,
    ) -> None:
        self._corpus_dir = corpus_dir
        self._store = GuidelineIndexStore(index_path)
        self._provider = provider
        self._embed_model = embed_model
        self._embed_dimension = embed_dimension
        self._keyword_top_k = keyword_top_k
        self._dense_top_k = dense_top_k
        self._rerank_candidate_limit = rerank_candidate_limit
        self._chunks: list[GuidelineChunk] | None = None
        self._bm25: Bm25Index | None = None

    async def search(
        self,
        query: str,
        *,
        category: str | None = None,
        topic_tag: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> GuidelineSearchResponse:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("query is required")
        bounded_limit = max(1, min(limit, MAX_LIMIT))
        chunks = self._load_chunks()
        if not chunks:
            return GuidelineSearchResponse(
                results=[],
                warnings=["clinical_guideline_corpus_empty"],
            )

        keyword_ranked = self._keyword_results(
            clean_query,
            category=category,
            topic_tag=topic_tag,
        )
        dense_ranked, dense_warnings = await self._dense_results(
            clean_query,
            chunks,
            category=category,
            topic_tag=topic_tag,
        )
        warnings = dense_warnings

        fused = _fuse_results(keyword_ranked, dense_ranked)
        if not fused:
            return GuidelineSearchResponse(results=[], warnings=warnings)

        candidate_chunks = [chunk for chunk, _score in fused[: self._rerank_candidate_limit]]
        if dense_warnings:
            reranked = [(chunk, score) for chunk, score in fused[:bounded_limit]]
            rerank_warnings: list[str] = []
        else:
            reranked, rerank_warnings = await self._rerank(
                clean_query,
                candidate_chunks,
                fallback_scores=dict(fused),
                limit=bounded_limit,
            )
        warnings.extend(rerank_warnings)
        retrieval_method = _retrieval_method(
            provider_configured=self._provider is not None,
            dense_failed=bool(dense_warnings),
            rerank_failed=bool(rerank_warnings),
        )
        results = [
            GuidelineSearchResult(
                chunk=chunk,
                score=score,
                snippet=_snippet(chunk.text, clean_query),
                retrieval_method=retrieval_method,
            )
            for chunk, score in reranked[:bounded_limit]
        ]
        if self._provider is None:
            results = [
                GuidelineSearchResult(
                    chunk=result.chunk,
                    score=result.score,
                    snippet=result.snippet,
                    retrieval_method="keyword_only",
                )
                for result in results
            ]
            warnings.append("cohere_not_configured_keyword_only")
        return GuidelineSearchResponse(results=results, warnings=warnings)

    def _load_chunks(self) -> list[GuidelineChunk]:
        if self._chunks is not None:
            return self._chunks
        documents = load_guideline_documents(self._corpus_dir)
        chunks = chunk_guideline_documents(documents)
        self._store.sync_chunks(chunks)
        self._chunks = chunks
        self._bm25 = Bm25Index(chunks)
        return chunks

    def _keyword_results(
        self,
        query: str,
        *,
        category: str | None,
        topic_tag: str | None,
    ) -> list[RankedChunk]:
        if self._bm25 is None:
            self._load_chunks()
        if self._bm25 is None:
            return []
        return self._bm25.search(
            query,
            category=category,
            topic_tag=topic_tag,
            limit=self._keyword_top_k,
        )

    async def _dense_results(
        self,
        query: str,
        chunks: list[GuidelineChunk],
        *,
        category: str | None,
        topic_tag: str | None,
    ) -> tuple[list[RankedChunk], list[str]]:
        if self._provider is None:
            return [], []
        filtered_chunks = [
            chunk
            for chunk in chunks
            if _matches_filters(chunk, category=category, topic_tag=topic_tag)
        ]
        if not filtered_chunks:
            return [], []

        try:
            embeddings = await self._ensure_embeddings(filtered_chunks)
            query_embedding = await self._provider.embed_query(query)
        except GuidelineProviderError:
            return [], ["cohere_embedding_failed_keyword_only"]

        scored: list[RankedChunk] = []
        for chunk in filtered_chunks:
            vector = embeddings.get(chunk.chunk_id)
            if vector is None:
                continue
            score = _cosine_similarity(query_embedding, vector)
            if score > 0:
                scored.append(RankedChunk(chunk=chunk, score=score))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: self._dense_top_k], []

    async def _ensure_embeddings(
        self,
        chunks: list[GuidelineChunk],
    ) -> dict[str, list[float]]:
        if self._provider is None:
            return {}
        chunk_ids = {chunk.chunk_id for chunk in chunks}
        cached = self._store.load_embeddings(
            model=self._embed_model,
            dimension=self._embed_dimension,
            chunk_ids=chunk_ids,
        )
        missing = [chunk for chunk in chunks if chunk.chunk_id not in cached]
        if not missing:
            return cached

        vectors = await self._provider.embed_documents([chunk.rerank_text() for chunk in missing])
        if len(vectors) != len(missing):
            raise GuidelineProviderError("embedding count did not match chunk count")
        new_embeddings = {
            chunk.chunk_id: vector for chunk, vector in zip(missing, vectors, strict=True)
        }
        self._store.store_embeddings(
            model=self._embed_model,
            dimension=self._embed_dimension,
            embeddings=new_embeddings,
        )
        return {**cached, **new_embeddings}

    async def _rerank(
        self,
        query: str,
        chunks: list[GuidelineChunk],
        *,
        fallback_scores: dict[GuidelineChunk, float],
        limit: int,
    ) -> tuple[list[tuple[GuidelineChunk, float]], list[str]]:
        if self._provider is None:
            return [(chunk, fallback_scores[chunk]) for chunk in chunks[:limit]], []
        try:
            results = await self._provider.rerank(
                query=query,
                documents=[chunk.rerank_text() for chunk in chunks],
                top_n=min(limit, len(chunks)),
            )
        except GuidelineProviderError:
            return (
                [(chunk, fallback_scores[chunk]) for chunk in chunks[:limit]],
                ["cohere_rerank_failed_fused_results"],
            )
        reranked: list[tuple[GuidelineChunk, float]] = []
        for result in results:
            if 0 <= result.index < len(chunks):
                reranked.append((chunks[result.index], result.relevance_score))
        if not reranked:
            return (
                [(chunk, fallback_scores[chunk]) for chunk in chunks[:limit]],
                ["cohere_rerank_empty_fused_results"],
            )
        return reranked, []


def _fuse_results(
    keyword_ranked: list[RankedChunk],
    dense_ranked: list[RankedChunk],
) -> list[tuple[GuidelineChunk, float]]:
    scores: dict[GuidelineChunk, float] = {}
    for ranked in (keyword_ranked, dense_ranked):
        for rank, result in enumerate(ranked, start=1):
            scores[result.chunk] = scores.get(result.chunk, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _retrieval_method(
    *,
    provider_configured: bool,
    dense_failed: bool,
    rerank_failed: bool,
) -> str:
    if not provider_configured or dense_failed:
        return "keyword_only"
    if rerank_failed:
        return "hybrid_fused"
    return "hybrid_rerank"


def _matches_filters(
    chunk: GuidelineChunk,
    *,
    category: str | None,
    topic_tag: str | None,
) -> bool:
    if category is not None and chunk.metadata.category != category:
        return False
    if topic_tag is None:
        return True
    target = topic_tag.lower()
    return any(target in tag.lower() for tag in chunk.metadata.topic_tags)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _snippet(text: str, query: str, *, max_chars: int = 700) -> str:
    terms = [term for term in tokenize(query) if len(term) >= MIN_SNIPPET_TERM_LENGTH]
    lower_text = text.lower()
    first_index = min(
        (lower_text.find(term) for term in terms if lower_text.find(term) >= 0),
        default=0,
    )
    start = max(0, first_index - 160)
    end = min(len(text), start + max_chars)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet


def publication_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=UTC)
