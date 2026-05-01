"""Optional Langfuse tracing bridge.

The sidecar keeps its local ``TraceCollector`` as the audit-facing source of
truth. This module mirrors the same request into Langfuse when credentials are
present so demo runs can inspect the full prompt/tool/verifier timeline.
Missing credentials, disabled tracing, or SDK failures degrade to no-op.
"""

from __future__ import annotations

import contextvars
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import cache
from typing import Any

import structlog

DEFAULT_LANGFUSE_BASE_URL = "https://us.cloud.langfuse.com"

_OBSERVATION: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "oe_ai_agent_langfuse_observation",
    default=None,
)


@dataclass(frozen=True)
class LangfuseSettings:
    enabled: bool
    public_key: str | None
    secret_key: str | None
    base_url: str
    environment: str | None
    release: str | None
    sample_rate: float
    flush_on_request: bool

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.public_key and self.secret_key)


class LangfuseObservation:
    """Small wrapper that makes SDK observations safe to call from app code."""

    def __init__(self, observation: Any | None) -> None:
        self._observation = observation

    @property
    def active(self) -> bool:
        return self._observation is not None

    def update(self, **fields: Any) -> None:
        if self._observation is None:
            return
        payload = {
            key: _jsonable(value) for key, value in fields.items() if value is not None
        }
        if not payload:
            return
        try:
            self._observation.update(**payload)
        except Exception as exc:
            _logger().warning("langfuse.observation_update_failed", error=_error(exc))


@cache
def settings_from_env() -> LangfuseSettings:
    return LangfuseSettings(
        enabled=_parse_bool(os.environ.get("LANGFUSE_TRACING_ENABLED"), default=True),
        public_key=_blank_to_none(os.environ.get("LANGFUSE_PUBLIC_KEY")),
        secret_key=_blank_to_none(os.environ.get("LANGFUSE_SECRET_KEY")),
        base_url=os.environ.get("LANGFUSE_BASE_URL") or DEFAULT_LANGFUSE_BASE_URL,
        environment=_blank_to_none(os.environ.get("LANGFUSE_ENVIRONMENT")),
        release=_blank_to_none(os.environ.get("LANGFUSE_RELEASE")),
        sample_rate=_parse_float(os.environ.get("LANGFUSE_SAMPLE_RATE"), default=1.0),
        flush_on_request=_parse_bool(
            os.environ.get("LANGFUSE_FLUSH_ON_REQUEST"),
            default=True,
        ),
    )


@cache
def _client() -> Any | None:
    settings = settings_from_env()
    if not settings.configured:
        return None

    try:
        from langfuse import Langfuse  # noqa: PLC0415
    except ImportError as exc:
        _logger().warning("langfuse.sdk_missing", error=_error(exc))
        return None

    try:
        return Langfuse(
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            base_url=settings.base_url,
            environment=settings.environment,
            release=settings.release,
            sample_rate=settings.sample_rate,
        )
    except Exception as exc:
        _logger().warning("langfuse.client_init_failed", error=_error(exc))
        return None


@asynccontextmanager
async def request_trace(
    *,
    name: str,
    request_id: str,
    patient_uuid: str,
    model_id: str,
    action: str,
    input_payload: Mapping[str, Any],
    conversation_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    tags: list[str] | None = None,
) -> AsyncIterator[LangfuseObservation]:
    """Create one Langfuse root trace for a sidecar request."""

    client = _client()
    if client is None:
        yield LangfuseObservation(None)
        return

    settings = settings_from_env()
    trace_metadata: dict[str, Any] = {
        "request_id": request_id,
        "patient_uuid": patient_uuid,
        "model_id": model_id,
        "action": action,
        "environment": settings.environment,
        **dict(metadata or {}),
    }
    if conversation_id is not None:
        trace_metadata["conversation_id"] = conversation_id

    try:
        root_cm = client.start_as_current_observation(
            name=name,
            as_type="agent",
            input=_jsonable(input_payload),
            metadata=_jsonable({**trace_metadata, "tags": tags or []}),
        )
        root = root_cm.__enter__()
    except Exception as exc:
        _logger().warning("langfuse.request_trace_start_failed", error=_error(exc))
        yield LangfuseObservation(None)
        return

    token = _OBSERVATION.set(root)
    handle = LangfuseObservation(root)
    exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
        None,
        None,
        None,
    )
    try:
        try:
            yield handle
        except BaseException as exc:
            exc_info = sys.exc_info()
            handle.update(metadata={"status": "error", "error": _error(exc)})
            raise
    finally:
        _OBSERVATION.reset(token)
        with suppress(Exception):
            root_cm.__exit__(*exc_info)
        if settings.flush_on_request:
            flush()


@asynccontextmanager
async def observation(
    *,
    name: str,
    as_type: str = "span",
    input_payload: Any | None = None,
    model: str | None = None,
    model_parameters: Mapping[str, Any] | None = None,
) -> AsyncIterator[LangfuseObservation]:
    """Create a child observation under the active Langfuse trace."""

    parent = _OBSERVATION.get()
    if parent is None:
        yield LangfuseObservation(None)
        return

    try:
        child_cm = parent.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=_jsonable(input_payload),
            model=model,
            model_parameters=_jsonable(model_parameters),
        )
        child = child_cm.__enter__()
    except Exception as exc:
        _logger().warning(
            "langfuse.child_observation_start_failed",
            observation=name,
            error=_error(exc),
        )
        yield LangfuseObservation(None)
        return

    token = _OBSERVATION.set(child)
    handle = LangfuseObservation(child)
    exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
        None,
        None,
        None,
    )
    try:
        try:
            yield handle
        except BaseException as exc:
            exc_info = sys.exc_info()
            handle.update(metadata={"status": "error", "error": _error(exc)})
            raise
    finally:
        _OBSERVATION.reset(token)
        with suppress(Exception):
            child_cm.__exit__(*exc_info)


def update_current_observation(**fields: Any) -> None:
    LangfuseObservation(_OBSERVATION.get()).update(**fields)


def flush() -> None:
    client = _client()
    if client is None:
        return
    with suppress(Exception):
        client.flush()


def shutdown() -> None:
    client = _client()
    if client is None:
        return
    with suppress(Exception):
        client.flush()
    with suppress(Exception):
        client.shutdown()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(inner) for key, inner in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(inner) for inner in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    return str(value)


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(value: str | None, *, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _logger() -> Any:
    return structlog.get_logger(__name__)


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:400]
