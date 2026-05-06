"""Data models for the local clinical guideline corpus."""

from __future__ import annotations

from dataclasses import dataclass

GLOBAL_EVIDENCE_PATIENT_ID = "__global__"
GUIDELINE_RESOURCE_TYPE = "ClinicalGuidelineChunk"


@dataclass(frozen=True)
class GuidelineMetadata:
    source_organization: str
    title: str
    publication_date: str
    grade: str | None
    population: str | None
    source_url: str | None
    license: str | None
    topic_tags: tuple[str, ...]
    file_path: str
    category: str
    status: str | None
    note: str | None
    authors: str | None
    secondary_url: str | None


@dataclass(frozen=True)
class GuidelineDocument:
    metadata: GuidelineMetadata
    body: str
    content_hash: str


@dataclass(frozen=True)
class GuidelineChunk:
    chunk_id: str
    metadata: GuidelineMetadata
    section_path: str
    text: str
    search_text: str
    content_hash: str

    def rerank_text(self) -> str:
        parts = [
            f"Title: {self.metadata.title}",
            f"Source: {self.metadata.source_organization}",
            f"Date: {self.metadata.publication_date}",
            f"Category: {self.metadata.category}",
            f"Tags: {', '.join(self.metadata.topic_tags)}",
            f"Population: {self.metadata.population or ''}",
            f"Section: {self.section_path}",
            self.text,
        ]
        return "\n".join(part for part in parts if part.strip())


@dataclass(frozen=True)
class RerankResult:
    index: int
    relevance_score: float
