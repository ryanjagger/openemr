"""Tests for the optional Langfuse bridge."""

from __future__ import annotations

import sys
import types
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


class _FakePropagateContext:
    def __init__(self, fields: dict[str, Any], log: list[_FakePropagateContext]) -> None:
        self.fields = fields
        self._log = log
        self.entered = False
        self.exited = False

    def __enter__(self) -> _FakePropagateContext:
        self.entered = True
        self._log.append(self)
        return self

    def __exit__(self, *_exc: object) -> Literal[False]:
        self.exited = True
        return False


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


def _install_fake_langfuse_module(
    monkeypatch: pytest.MonkeyPatch,
) -> list[_FakePropagateContext]:
    """Replace ``langfuse.propagate_attributes`` with a recording fake.

    ``request_trace`` does ``from langfuse import propagate_attributes`` at
    call-time, so we patch the module rather than the import site to keep
    the fake in scope regardless of import order.
    """
    calls: list[_FakePropagateContext] = []

    def _fake_propagate(**fields: Any) -> _FakePropagateContext:
        return _FakePropagateContext(fields, calls)

    fake_pkg = types.ModuleType("langfuse")
    fake_pkg.propagate_attributes = _fake_propagate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_pkg)
    return calls


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
    propagate_calls = _install_fake_langfuse_module(monkeypatch)
    langfuse_module.settings_from_env.cache_clear()

    async with request_trace(
        name="chat.turn",
        request_id="req-2",
        conversation_id="conv-1",
        patient_uuid="patient-2",
        model_id="mock",
        action="chat.turn",
        input_payload={"messages": [{"role": "user", "content": "hi"}]},
        user_id="user-uuid-7",
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
    assert root.fields["metadata"]["user_id"] == "user-uuid-7"
    assert root.fields["metadata"]["session_id"] == "conv-1"
    assert root.fields["metadata"]["tags"] == ["chat", "demo"]
    assert root.updates[-1]["output"] == {"done": True}

    assert len(propagate_calls) == 1
    propagate = propagate_calls[0]
    assert propagate.entered is True
    assert propagate.exited is True
    assert propagate.fields["user_id"] == "user-uuid-7"
    assert propagate.fields["session_id"] == "conv-1"
    assert propagate.fields["tags"] == ["chat", "demo"]

    assert len(root.children) == 1
    child_observation = root.children[0]
    assert child_observation.fields["name"] == "tool.get_lab_trend"
    assert child_observation.fields["as_type"] == "tool"
    assert child_observation.updates[-1]["output"] == {"rows": []}


@pytest.mark.asyncio
async def test_request_trace_explicit_session_id_overrides_conversation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brief requests pass an explicit session_id that should win over conversation_id."""
    fake = _FakeClient()
    monkeypatch.setattr(langfuse_module, "_client", lambda: fake)
    propagate_calls = _install_fake_langfuse_module(monkeypatch)
    langfuse_module.settings_from_env.cache_clear()

    async with request_trace(
        name="brief.read",
        request_id="req-3",
        conversation_id="conv-should-be-ignored",
        patient_uuid="patient-3",
        model_id="mock",
        action="brief.read",
        input_payload={"patient_uuid": "patient-3"},
        user_id="user-uuid-9",
        session_id="login-session-abc",
        tags=["brief", "demo"],
    ):
        pass

    assert len(propagate_calls) == 1
    propagate = propagate_calls[0]
    assert propagate.fields["session_id"] == "login-session-abc"

    root = fake.roots[0]
    assert root.fields["metadata"]["session_id"] == "login-session-abc"
    assert root.fields["metadata"]["conversation_id"] == "conv-should-be-ignored"
