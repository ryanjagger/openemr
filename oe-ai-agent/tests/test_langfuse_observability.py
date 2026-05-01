"""Tests for the optional Langfuse bridge."""

from __future__ import annotations

from typing import Any, Literal

import pytest

from oe_ai_agent.observability import langfuse as langfuse_module
from oe_ai_agent.observability.langfuse import observation, request_trace


class _FakeContext:
    def __init__(self, observation: _FakeObservation) -> None:
        self.observation = observation

    def __enter__(self) -> _FakeObservation:
        return self.observation

    def __exit__(self, *_exc: object) -> Literal[False]:
        return False


class _FakeObservation:
    def __init__(self, **fields: Any) -> None:
        self.fields = fields
        self.children: list[_FakeObservation] = []
        self.updates: list[dict[str, Any]] = []

    def start_as_current_observation(self, **fields: Any) -> _FakeContext:
        child = _FakeObservation(**fields)
        self.children.append(child)
        return _FakeContext(child)

    def update(self, **fields: Any) -> None:
        self.updates.append(fields)


class _FakeClient:
    def __init__(self) -> None:
        self.roots: list[_FakeObservation] = []
        self.flush_count = 0

    def start_as_current_observation(self, **fields: Any) -> _FakeContext:
        root = _FakeObservation(**fields)
        self.roots.append(root)
        return _FakeContext(root)

    def flush(self) -> None:
        self.flush_count += 1


@pytest.mark.asyncio
async def test_request_trace_is_noop_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    langfuse_module.settings_from_env.cache_clear()
    langfuse_module._client.cache_clear()

    async with request_trace(
        name="brief.read",
        request_id="req-1",
        patient_uuid="patient-1",
        model_id="mock",
        action="brief.read",
        input_payload={"patient_uuid": "patient-1"},
    ) as trace:
        assert not trace.active
        trace.update(output={"ok": True})


@pytest.mark.asyncio
async def test_request_trace_records_root_child_and_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(langfuse_module, "_client", lambda: fake)
    langfuse_module.settings_from_env.cache_clear()

    async with request_trace(
        name="chat.turn",
        request_id="req-2",
        conversation_id="conv-1",
        patient_uuid="patient-2",
        model_id="mock",
        action="chat.turn",
        input_payload={"messages": [{"role": "user", "content": "hi"}]},
        tags=["chat", "demo"],
    ) as trace:
        assert trace.active
        async with observation(
            name="tool.get_lab_trend",
            as_type="tool",
            input_payload={"code_or_text": "a1c"},
        ) as child:
            child.update(output={"rows": []}, metadata={"status": "ok"})
        trace.update(output={"done": True}, metadata={"status": "ok"})

    assert fake.flush_count == 1
    assert len(fake.roots) == 1
    root = fake.roots[0]
    assert root.fields["name"] == "chat.turn"
    assert root.fields["as_type"] == "agent"
    assert root.fields["metadata"]["request_id"] == "req-2"
    assert root.fields["metadata"]["conversation_id"] == "conv-1"
    assert root.fields["metadata"]["tags"] == ["chat", "demo"]
    assert root.updates[-1]["output"] == {"done": True}

    assert len(root.children) == 1
    child_observation = root.children[0]
    assert child_observation.fields["name"] == "tool.get_lab_trend"
    assert child_observation.fields["as_type"] == "tool"
    assert child_observation.updates[-1]["output"] == {"rows": []}
