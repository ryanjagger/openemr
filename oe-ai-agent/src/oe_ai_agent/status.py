"""Live status for long-running chat turns.

The HTTP chat response is still a final JSON envelope, but the browser can
poll a small status endpoint using the same request_id while the LangGraph
turn is running. Status is intentionally best-effort and stored in a small
shared filesystem cache so status polls still work when the sidecar has more
than one worker process.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

STATUS_TTL_SECONDS = 600
STATUS_DIR_ENV = "OE_AI_AGENT_STATUS_DIR"

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "oe_ai_agent_status_request_id",
    default=None,
)
_STATUSES: dict[str, ChatRequestStatus] = {}
_LOCK = threading.Lock()


@dataclass
class ChatRequestStatus:
    request_id: str
    state: str
    stage: str
    detail: str | None = None
    worker: str | None = None
    route: str | None = None
    tool_name: str | None = None
    attrs: dict[str, object] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "state": self.state,
            "stage": self.stage,
            "detail": self.detail,
            "worker": self.worker,
            "route": self.route,
            "tool_name": self.tool_name,
            "attrs": dict(self.attrs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> ChatRequestStatus | None:
        if not isinstance(value, dict):
            return None

        request_id = value.get("request_id")
        state = value.get("state")
        stage = value.get("stage")
        if (
            not isinstance(request_id, str)
            or not isinstance(state, str)
            or not isinstance(stage, str)
        ):
            return None

        attrs_value = value.get("attrs")
        attrs: dict[str, object] = {}
        if isinstance(attrs_value, dict):
            attrs = {str(key): attr_value for key, attr_value in attrs_value.items()}

        created_at = _float_or_now(value.get("created_at"))
        updated_at = _float_or_now(value.get("updated_at"))
        return cls(
            request_id=request_id,
            state=state,
            stage=stage,
            detail=_optional_string(value.get("detail")),
            worker=_optional_string(value.get("worker")),
            route=_optional_string(value.get("route")),
            tool_name=_optional_string(value.get("tool_name")),
            attrs=attrs,
            created_at=created_at,
            updated_at=updated_at,
        )


@contextmanager
def chat_status_context(request_id: str, *, stage: str) -> Iterator[None]:
    token = _REQUEST_ID.set(request_id)
    update_chat_status(request_id, stage=stage)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def update_current_chat_status(
    *,
    stage: str,
    detail: str | None = None,
    worker: str | None = None,
    route: str | None = None,
    tool_name: str | None = None,
    attrs: dict[str, object] | None = None,
) -> None:
    request_id = _REQUEST_ID.get()
    if request_id is None:
        return
    update_chat_status(
        request_id,
        stage=stage,
        detail=detail,
        worker=worker,
        route=route,
        tool_name=tool_name,
        attrs=attrs,
    )


def complete_current_chat_status(stage: str = "Complete") -> None:
    request_id = _REQUEST_ID.get()
    if request_id is None:
        return
    update_chat_status(request_id, state="completed", stage=stage)


def fail_current_chat_status(stage: str, detail: str | None = None) -> None:
    request_id = _REQUEST_ID.get()
    if request_id is None:
        return
    update_chat_status(request_id, state="error", stage=stage, detail=detail)


def update_chat_status(
    request_id: str,
    *,
    state: str = "running",
    stage: str,
    detail: str | None = None,
    worker: str | None = None,
    route: str | None = None,
    tool_name: str | None = None,
    attrs: dict[str, object] | None = None,
) -> None:
    now = time.time()
    with _LOCK:
        _prune_locked(now)
        existing = _STATUSES.get(request_id)
        if existing is None:
            status = ChatRequestStatus(
                request_id=request_id,
                state=state,
                stage=stage,
                detail=detail,
                worker=worker,
                route=route,
                tool_name=tool_name,
                attrs=attrs or {},
                created_at=now,
                updated_at=now,
            )
            _STATUSES[request_id] = status
        else:
            existing.state = state
            existing.stage = stage
            existing.detail = detail
            existing.worker = worker
            existing.route = route
            existing.tool_name = tool_name
            existing.attrs = attrs or {}
            existing.updated_at = now
            status = existing
        _write_status_locked(status)


def get_chat_status(request_id: str) -> dict[str, object]:
    now = time.time()
    with _LOCK:
        _prune_locked(now)
        status = _STATUSES.get(request_id)
        if status is None:
            status = _read_status_locked(request_id, now)
        if status is None:
            return {
                "request_id": request_id,
                "state": "unknown",
                "stage": "Unknown",
                "detail": None,
                "worker": None,
                "route": None,
                "tool_name": None,
                "attrs": {},
                "created_at": None,
                "updated_at": None,
            }
        return status.to_dict()


def _prune_locked(now: float) -> None:
    expired = [
        request_id
        for request_id, status in _STATUSES.items()
        if now - status.updated_at > STATUS_TTL_SECONDS
    ]
    for request_id in expired:
        del _STATUSES[request_id]
        _unlink_status_file_locked(request_id)

    try:
        status_dir = _status_dir()
        for path in status_dir.glob("*.json"):
            if now - path.stat().st_mtime > STATUS_TTL_SECONDS:
                path.unlink(missing_ok=True)
        for path in status_dir.glob("*.tmp.*"):
            if now - path.stat().st_mtime > STATUS_TTL_SECONDS:
                path.unlink(missing_ok=True)
    except OSError:
        return


def _write_status_locked(status: ChatRequestStatus) -> None:
    try:
        status_dir = _status_dir()
        status_dir.mkdir(parents=True, exist_ok=True)
        path = _status_path(status.request_id)
        tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        tmp_path.write_text(json.dumps(status.to_dict(), default=str), encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        return


def _read_status_locked(request_id: str, now: float) -> ChatRequestStatus | None:
    try:
        status = ChatRequestStatus.from_dict(
            json.loads(_status_path(request_id).read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, OSError):
        return None

    if status is None or now - status.updated_at > STATUS_TTL_SECONDS:
        _unlink_status_file_locked(request_id)
        return None

    _STATUSES[request_id] = status
    return status


def _unlink_status_file_locked(request_id: str) -> None:
    try:
        _status_path(request_id).unlink(missing_ok=True)
    except OSError:
        return


def _status_path(request_id: str) -> Path:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return _status_dir() / f"{digest}.json"


def _status_dir() -> Path:
    configured = os.environ.get(STATUS_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "oe-ai-agent-chat-status"


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _float_or_now(value: object) -> float:
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return float(value)
    return time.time()
