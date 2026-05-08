from __future__ import annotations

from oe_ai_agent.errors import MODEL_OVERLOADED_DETAIL, classify_chat_exception, summarize_error


def test_classifies_anthropic_overloaded_error_as_retryable() -> None:
    exc = RuntimeError(
        'litellm.InternalServerError: AnthropicError - {"type":"error",'
        '"error":{"type":"overloaded_error","message":"Overloaded"},'
        '"request_id":"req_011CaqdBBG4UAZFCXKk9NAuA"}'
    )

    error = classify_chat_exception(exc)

    assert error.rule == "model_overloaded"
    assert error.detail == MODEL_OVERLOADED_DETAIL
    assert error.status == "error"
    assert error.status_stage == "Model provider overloaded"
    assert error.http_status == 503


def test_non_provider_exception_remains_agent_error() -> None:
    exc = RuntimeError("parser exploded")

    error = classify_chat_exception(exc)

    assert error.rule == "agent_error"
    assert error.detail == "parser exploded"
    assert error.http_status is None


def test_summarize_error_strips_litellm_prefix() -> None:
    exc = RuntimeError("wrapper: litellm.InternalServerError: provider body")

    assert summarize_error(exc) == "InternalServerError: provider body"
