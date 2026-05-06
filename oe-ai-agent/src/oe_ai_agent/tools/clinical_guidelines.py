"""Chat tool for hybrid retrieval over the clinical guideline corpus."""

from __future__ import annotations

from oe_ai_agent.guidelines.cohere import CohereGuidelineClient
from oe_ai_agent.guidelines.models import (
    GLOBAL_EVIDENCE_PATIENT_ID,
    GUIDELINE_RESOURCE_TYPE,
)
from oe_ai_agent.guidelines.retriever import (
    ClinicalGuidelineRetriever,
    publication_datetime,
)
from oe_ai_agent.guidelines.settings import load_guideline_settings
from oe_ai_agent.schemas.tool_results import TypedRow

DEFAULT_GUIDELINE_LIMIT = 6

_DEFAULT_RETRIEVER: ClinicalGuidelineRetriever | None = None


async def search_clinical_guidelines(
    *,
    query: str,
    category: str | None = None,
    topic_tag: str | None = None,
    limit: int = DEFAULT_GUIDELINE_LIMIT,
) -> list[TypedRow]:
    retriever = _default_retriever()
    response = await retriever.search(
        query,
        category=category,
        topic_tag=topic_tag,
        limit=limit,
    )
    return [
        TypedRow(
            resource_type=GUIDELINE_RESOURCE_TYPE,
            resource_id=result.chunk.chunk_id,
            patient_id=GLOBAL_EVIDENCE_PATIENT_ID,
            last_updated=publication_datetime(result.chunk.metadata.publication_date),
            fields={
                "source": "clinical_guideline_corpus",
                "title": result.chunk.metadata.title,
                "source_organization": result.chunk.metadata.source_organization,
                "publication_date": result.chunk.metadata.publication_date,
                "grade": result.chunk.metadata.grade,
                "population": result.chunk.metadata.population,
                "source_url": result.chunk.metadata.source_url,
                "secondary_url": result.chunk.metadata.secondary_url,
                "license": result.chunk.metadata.license,
                "topic_tags": list(result.chunk.metadata.topic_tags),
                "category": result.chunk.metadata.category,
                "status": result.chunk.metadata.status,
                "note": result.chunk.metadata.note,
                "file_path": result.chunk.metadata.file_path,
                "section_path": result.chunk.section_path,
                "retrieval_score": result.score,
                "retrieval_method": result.retrieval_method,
                "retrieval_warnings": response.warnings,
            },
            verbatim_excerpt=result.snippet,
        )
        for result in response.results
    ]


def _default_retriever() -> ClinicalGuidelineRetriever:
    global _DEFAULT_RETRIEVER  # noqa: PLW0603 - sidecar-local lazy singleton.
    if _DEFAULT_RETRIEVER is not None:
        return _DEFAULT_RETRIEVER
    settings = load_guideline_settings()
    provider = (
        CohereGuidelineClient(
            api_key=settings.cohere_api_key,
            base_url=settings.cohere_base_url,
            embed_model=settings.embed_model,
            embed_dimension=settings.embed_dimension,
            rerank_model=settings.rerank_model,
        )
        if settings.cohere_api_key is not None
        else None
    )
    _DEFAULT_RETRIEVER = ClinicalGuidelineRetriever(
        corpus_dir=settings.corpus_dir,
        index_path=settings.index_path,
        provider=provider,
        embed_model=settings.embed_model,
        embed_dimension=settings.embed_dimension,
        keyword_top_k=settings.keyword_top_k,
        dense_top_k=settings.dense_top_k,
        rerank_candidate_limit=settings.rerank_candidate_limit,
    )
    return _DEFAULT_RETRIEVER


def reset_guideline_retriever_for_tests() -> None:
    global _DEFAULT_RETRIEVER  # noqa: PLW0603 - explicit test helper.
    _DEFAULT_RETRIEVER = None
