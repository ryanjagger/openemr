"""Runtime settings for clinical guideline retrieval."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuidelineSettings:
    corpus_dir: Path
    index_path: Path
    cohere_api_key: str | None
    cohere_base_url: str
    embed_model: str
    embed_dimension: int
    rerank_model: str
    keyword_top_k: int
    dense_top_k: int
    rerank_candidate_limit: int


def load_guideline_settings() -> GuidelineSettings:
    sidecar_root = Path(__file__).resolve().parents[3]
    corpus_dir = Path(
        os.environ.get(
            "AI_AGENT_GUIDELINE_CORPUS_DIR",
            str(sidecar_root / "corpora" / "clinical-guidelines"),
        )
    )
    index_path = _index_path(
        os.environ.get("AI_AGENT_GUIDELINE_INDEX_DIR"),
        default=sidecar_root / ".rag_cache" / "clinical_guidelines.sqlite",
    )
    return GuidelineSettings(
        corpus_dir=corpus_dir,
        index_path=index_path,
        cohere_api_key=_optional_env("COHERE_API_KEY"),
        cohere_base_url=os.environ.get("COHERE_BASE_URL", "https://api.cohere.com"),
        embed_model=os.environ.get("AI_AGENT_GUIDELINE_EMBED_MODEL", "embed-v4.0"),
        embed_dimension=_parse_int(
            os.environ.get("AI_AGENT_GUIDELINE_EMBED_DIM"),
            default=1024,
        ),
        rerank_model=os.environ.get(
            "AI_AGENT_GUIDELINE_RERANK_MODEL",
            "rerank-v4.0-fast",
        ),
        keyword_top_k=_parse_int(os.environ.get("AI_AGENT_GUIDELINE_KEYWORD_TOP_K"), default=25),
        dense_top_k=_parse_int(os.environ.get("AI_AGENT_GUIDELINE_DENSE_TOP_K"), default=25),
        rerank_candidate_limit=_parse_int(
            os.environ.get("AI_AGENT_GUIDELINE_RERANK_CANDIDATE_LIMIT"),
            default=40,
        ),
    )


def _index_path(value: str | None, *, default: Path) -> Path:
    if value is None or not value.strip():
        return default
    configured = Path(value)
    if configured.suffix in {".db", ".sqlite", ".sqlite3"}:
        return configured
    return configured / "clinical_guidelines.sqlite"


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default
