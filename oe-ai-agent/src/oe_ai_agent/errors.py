"""Error classification helpers for user-facing agent surfaces."""

from __future__ import annotations

from dataclasses import dataclass

MODEL_OVERLOADED_DETAIL = (
    "The AI model provider is temporarily overloaded. Please try again in a moment."
)


@dataclass(frozen=True)
class ChatErrorClassification:
    rule: str
    detail: str
    status: str
    status_stage: str
    http_status: int | None = None


def classify_chat_exception(exc: BaseException) -> ChatErrorClassification:
    text = _exception_chain_text(exc).lower()
    if "overloaded_error" in text or ("anthropic" in text and "overloaded" in text):
        return ChatErrorClassification(
            rule="model_overloaded",
            detail=MODEL_OVERLOADED_DETAIL,
            status="error",
            status_stage="Model provider overloaded",
            http_status=503,
        )

    return ChatErrorClassification(
        rule="agent_error",
        detail=summarize_error(exc),
        status="error",
        status_stage="Chat graph failed",
    )


def summarize_error(exc: BaseException) -> str:
    """Compact, user-safe error string for the panel and trace metadata.

    LiteLLM exception messages start with ``litellm.<ErrorType>: <provider>: <body>``
    and contain the upstream JSON. We keep the provider type + body but strip
    leading qualifiers so the panel shows something readable.
    """
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    for prefix in ("litellm.",):
        idx = text.find(prefix)
        if idx >= 0:
            text = text[idx + len(prefix) :]
            break
    return text[:400]


def _exception_chain_text(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return "\n".join(parts)
