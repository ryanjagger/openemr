"""Tests for clinical guideline hybrid retrieval."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oe_ai_agent.agent.nodes.verify_chat import _verify_chat_facts
from oe_ai_agent.guidelines.cohere import GuidelineProviderError
from oe_ai_agent.guidelines.corpus import (
    chunk_guideline_documents,
    load_guideline_documents,
)
from oe_ai_agent.guidelines.models import (
    GLOBAL_EVIDENCE_PATIENT_ID,
    GUIDELINE_RESOURCE_TYPE,
    RerankResult,
)
from oe_ai_agent.guidelines.retriever import ClinicalGuidelineRetriever
from oe_ai_agent.llm.client import LlmToolCall
from oe_ai_agent.schemas.chat import ChatFact, ChatFactType
from oe_ai_agent.schemas.tool_results import TypedRow
from oe_ai_agent.tools import FhirClient
from oe_ai_agent.tools.chat_registry import execute_chat_tool
from oe_ai_agent.tools.clinical_guidelines import reset_guideline_retriever_for_tests

PATIENT = "patient-uuid-1"
FHIR_BASE = "http://fhir.test/apis/default/fhir"


class FakeProvider:
    def __init__(self) -> None:
        self.embedded_document_count = 0
        self.reranked = False

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded_document_count += len(texts)
        return [_vector_for_text(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return _vector_for_text(text)

    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> list[RerankResult]:
        del query
        self.reranked = True
        return [
            RerankResult(index=index, relevance_score=1.0 - (index * 0.01))
            for index, _document in enumerate(documents[:top_n])
        ]


class FailingProvider(FakeProvider):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        del texts
        raise GuidelineProviderError("embed failed")


def test_guideline_corpus_parses_front_matter_and_chunks(tmp_path: Path) -> None:
    _write_sample_corpus(tmp_path)

    documents = load_guideline_documents(tmp_path)
    chunks = chunk_guideline_documents(documents)

    assert len(documents) == 2
    assert chunks
    by_title = {chunk.metadata.title: chunk for chunk in chunks}
    assert "Adult Obesity Behavioral Interventions" in by_title
    assert by_title["Adult Obesity Behavioral Interventions"].metadata.topic_tags == (
        "obesity",
        "weight management",
        "DPP",
    )
    assert "Recommendation Summary" in {chunk.section_path.split(" > ")[-1] for chunk in chunks}


@pytest.mark.asyncio
async def test_keyword_only_guideline_search_returns_metadata(tmp_path: Path) -> None:
    _write_sample_corpus(tmp_path)
    retriever = ClinicalGuidelineRetriever(
        corpus_dir=tmp_path,
        index_path=tmp_path / "index.sqlite",
        provider=None,
        embed_model="embed-test",
        embed_dimension=3,
    )

    response = await retriever.search("overweight diabetes prevention program", limit=3)

    assert response.results
    assert response.results[0].retrieval_method == "keyword_only"
    assert response.results[0].chunk.metadata.category == "preventive"
    assert "diabetes prevention program" in response.results[0].snippet.lower()
    assert "cohere_not_configured_keyword_only" in response.warnings


@pytest.mark.asyncio
async def test_hybrid_guideline_search_uses_provider_and_rerank(tmp_path: Path) -> None:
    _write_sample_corpus(tmp_path)
    provider = FakeProvider()
    retriever = ClinicalGuidelineRetriever(
        corpus_dir=tmp_path,
        index_path=tmp_path / "index.sqlite",
        provider=provider,
        embed_model="embed-test",
        embed_dimension=3,
    )

    response = await retriever.search("breast cancer mammography screening age", limit=2)

    assert response.results
    assert response.results[0].retrieval_method == "hybrid_rerank"
    assert response.results[0].chunk.metadata.title == "Breast Cancer Screening"
    assert provider.embedded_document_count > 0
    assert provider.reranked
    assert response.warnings == []


@pytest.mark.asyncio
async def test_provider_failure_falls_back_to_keyword_only(tmp_path: Path) -> None:
    _write_sample_corpus(tmp_path)
    retriever = ClinicalGuidelineRetriever(
        corpus_dir=tmp_path,
        index_path=tmp_path / "index.sqlite",
        provider=FailingProvider(),
        embed_model="embed-test",
        embed_dimension=3,
    )

    response = await retriever.search("obesity behavioral counseling", limit=2)

    assert response.results
    assert response.results[0].retrieval_method == "keyword_only"
    assert "cohere_embedding_failed_keyword_only" in response.warnings


@pytest.mark.asyncio
async def test_chat_tool_returns_global_guideline_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_sample_corpus(tmp_path)
    monkeypatch.setenv("AI_AGENT_GUIDELINE_CORPUS_DIR", str(tmp_path))
    monkeypatch.setenv("AI_AGENT_GUIDELINE_INDEX_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    reset_guideline_retriever_for_tests()

    async with FhirClient(base_url=FHIR_BASE, bearer_token="t") as client:
        rows, error, payload = await execute_chat_tool(
            LlmToolCall(
                tool_call_id="t1",
                name="search_clinical_guidelines",
                arguments={"query": "breast cancer screening mammography", "limit": 2},
            ),
            client,
            PATIENT,
        )

    assert error is None
    assert rows
    assert rows[0].resource_type == GUIDELINE_RESOURCE_TYPE
    assert rows[0].patient_id == GLOBAL_EVIDENCE_PATIENT_ID
    assert rows[0].fields["source"] == "clinical_guideline_corpus"
    assert rows[0].verbatim_excerpt
    assert payload["rows"]
    reset_guideline_retriever_for_tests()


def test_guideline_fact_citations_are_global_not_patient_bound() -> None:
    row = TypedRow(
        resource_type=GUIDELINE_RESOURCE_TYPE,
        resource_id="guideline-1",
        patient_id=GLOBAL_EVIDENCE_PATIENT_ID,
        last_updated=datetime(2024, 4, 30, tzinfo=UTC),
        fields={
            "title": "Breast Cancer Screening",
            "publication_date": "2024-04-30",
        },
        verbatim_excerpt="Women aged 40 to 74 years should receive screening mammography.",
    )
    fact = ChatFact(
        type=ChatFactType.GUIDELINE,
        text="USPSTF breast cancer screening guidance was published on 2024-04-30.",
        verbatim_excerpts=["2024-04-30"],
        citations=[{"resource_type": GUIDELINE_RESOURCE_TYPE, "resource_id": "guideline-1"}],
    )

    verified, failures = _verify_chat_facts(
        [fact],
        [row],
        PATIENT,
        allowed_types=frozenset(ChatFactType),
    )

    assert verified == [fact]
    assert failures == []


def _write_sample_corpus(root: Path) -> None:
    preventive = root / "preventive"
    cancer = root / "cancer_screening"
    preventive.mkdir()
    cancer.mkdir()
    (preventive / "uspstf_obesity.md").write_text(
        """---
source_organization: U.S. Preventive Services Task Force (USPSTF)
title: Adult Obesity Behavioral Interventions
publication_date: 2018-09-18
grade: B
population: Adults with BMI 30 or higher
source_url: https://example.test/obesity
license: Public domain (US government work)
topic_tags: obesity, weight management, DPP
---

# Obesity in Adults

## Recommendation Summary

Adults with obesity should be offered or referred to intensive, multicomponent
behavioral interventions. A diabetes prevention program can support weight
management for eligible adults with prediabetes.
""",
        encoding="utf-8",
    )
    (cancer / "uspstf_breast_cancer_screening.md").write_text(
        """---
source_organization: U.S. Preventive Services Task Force (USPSTF)
title: Breast Cancer Screening
publication_date: 2024-04-30
grade: B
population: Women aged 40 to 74 years
source_url: https://example.test/breast-cancer-screening
license: Public domain (US government work)
topic_tags: breast cancer, mammography, screening
---

# Breast Cancer: Screening

## Recommendation Summary

Women aged 40 to 74 years should receive biennial screening mammography.
""",
        encoding="utf-8",
    )


def _vector_for_text(text: str) -> list[float]:
    lower = text.lower()
    return [
        1.0 if "breast" in lower or "mammography" in lower else 0.0,
        1.0 if "obesity" in lower or "diabetes prevention" in lower else 0.0,
        0.5,
    ]
