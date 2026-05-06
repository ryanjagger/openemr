"""Small in-process BM25 implementation for guideline keyword retrieval."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from oe_ai_agent.guidelines.models import GuidelineChunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class RankedChunk:
    chunk: GuidelineChunk
    score: float


class Bm25Index:
    def __init__(self, chunks: list[GuidelineChunk]) -> None:
        self._chunks = chunks
        self._term_counts = [Counter(tokenize(chunk.search_text)) for chunk in chunks]
        self._lengths = [sum(counts.values()) for counts in self._term_counts]
        self._average_length = (
            sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        )
        document_frequency: Counter[str] = Counter()
        for counts in self._term_counts:
            document_frequency.update(counts.keys())
        self._idf = {
            term: math.log(1.0 + (len(chunks) - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(
        self,
        query: str,
        *,
        category: str | None,
        topic_tag: str | None,
        limit: int,
    ) -> list[RankedChunk]:
        query_terms = tokenize(query)
        if not query_terms:
            return []
        scored: list[RankedChunk] = []
        for index, chunk in enumerate(self._chunks):
            if not _matches_filters(chunk, category=category, topic_tag=topic_tag):
                continue
            score = self._score(index, query_terms)
            if score > 0:
                scored.append(RankedChunk(chunk=chunk, score=score))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    def _score(self, index: int, query_terms: list[str]) -> float:
        counts = self._term_counts[index]
        document_length = self._lengths[index]
        if document_length == 0 or self._average_length == 0.0:
            return 0.0

        k1 = 1.5
        b = 0.75
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if frequency == 0:
                continue
            denominator = frequency + k1 * (
                1.0 - b + b * document_length / self._average_length
            )
            score += self._idf.get(term, 0.0) * frequency * (k1 + 1.0) / denominator
        return score


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


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
