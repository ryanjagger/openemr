"""Cohere HTTP client for guideline embeddings and reranking."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from oe_ai_agent.guidelines.models import RerankResult

MAX_EMBED_BATCH = 96


class GuidelineProviderError(RuntimeError):
    """Raised when an external guideline retrieval provider fails."""


class GuidelineRetrievalProvider(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> list[RerankResult]: ...


class CohereGuidelineClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        embed_model: str,
        embed_dimension: int,
        rerank_model: str,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._embed_model = embed_model
        self._embed_dimension = embed_dimension
        self._rerank_model = rerank_model
        self._timeout = timeout

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_EMBED_BATCH):
            batch = texts[start : start + MAX_EMBED_BATCH]
            vectors.extend(
                await self._embed_batch(batch, input_type="search_document")
            )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed_batch([text], input_type="search_query")
        if not vectors:
            raise GuidelineProviderError("Cohere returned no query embedding")
        return vectors[0]

    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> list[RerankResult]:
        if not documents:
            return []
        payload = {
            "model": self._rerank_model,
            "query": query,
            "documents": list(documents),
            "top_n": top_n,
        }
        data = await self._post_json("/v2/rerank", payload)
        results = data.get("results")
        if not isinstance(results, list):
            raise GuidelineProviderError("Cohere rerank response missing results")

        parsed: list[RerankResult] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            index = result.get("index")
            score = result.get("relevance_score")
            if isinstance(index, int) and isinstance(score, int | float):
                parsed.append(RerankResult(index=index, relevance_score=float(score)))
        return parsed

    async def _embed_batch(
        self,
        texts: Sequence[str],
        *,
        input_type: str,
    ) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self._embed_model,
            "input_type": input_type,
            "embedding_types": ["float"],
            "output_dimension": self._embed_dimension,
            "texts": list(texts),
        }
        data = await self._post_json("/v2/embed", payload)
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, dict):
            raise GuidelineProviderError("Cohere embed response missing embeddings")
        float_embeddings = embeddings.get("float")
        if not isinstance(float_embeddings, list):
            raise GuidelineProviderError("Cohere embed response missing float embeddings")

        vectors: list[list[float]] = []
        for embedding in float_embeddings:
            if not isinstance(embedding, list):
                raise GuidelineProviderError("Cohere returned an invalid embedding")
            vector: list[float] = []
            for value in embedding:
                if not isinstance(value, int | float):
                    raise GuidelineProviderError("Cohere returned a non-numeric embedding")
                vector.append(float(value))
            vectors.append(vector)
        if len(vectors) != len(texts):
            raise GuidelineProviderError("Cohere embedding count did not match input count")
        return vectors

    async def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Client-Name": "openemr-ai-agent-guideline-rag",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}{path}",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GuidelineProviderError("Cohere request failed") from exc
        if not isinstance(data, dict):
            raise GuidelineProviderError("Cohere returned a non-object response")
        return data
