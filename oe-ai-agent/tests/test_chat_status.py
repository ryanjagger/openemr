from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from oe_ai_agent import status as status_module
from oe_ai_agent.status import (
    chat_status_context,
    complete_current_chat_status,
    get_chat_status,
    update_current_chat_status,
)


@pytest.fixture(autouse=True)
def isolated_status_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(status_module.STATUS_DIR_ENV, str(tmp_path))
    status_module._STATUSES.clear()
    yield
    status_module._STATUSES.clear()


def test_chat_status_context_tracks_current_request() -> None:
    request_id = "00000000-0000-4000-8000-000000000001"

    with chat_status_context(request_id, stage="Starting chat turn"):
        update_current_chat_status(
            stage="Supervisor starting extractor worker",
            detail="Need uploaded document context",
            worker="supervisor",
            route="extractor",
        )

        status = get_chat_status(request_id)
        assert status["state"] == "running"
        assert status["stage"] == "Supervisor starting extractor worker"
        assert status["detail"] == "Need uploaded document context"
        assert status["worker"] == "supervisor"
        assert status["route"] == "extractor"

        complete_current_chat_status("Chat response ready")

    status = get_chat_status(request_id)
    assert status["state"] == "completed"
    assert status["stage"] == "Chat response ready"


def test_chat_status_is_read_from_shared_status_file() -> None:
    request_id = "00000000-0000-4000-8000-000000000002"

    with chat_status_context(request_id, stage="Starting chat turn"):
        update_current_chat_status(
            stage="Evidence retriever running",
            worker="evidence_retriever",
            attrs={"iteration": 2},
        )

    status_module._STATUSES.clear()

    status = get_chat_status(request_id)
    assert status["state"] == "running"
    assert status["stage"] == "Evidence retriever running"
    assert status["worker"] == "evidence_retriever"
    assert status["attrs"] == {"iteration": 2}


def test_unknown_chat_status_is_stable_shape() -> None:
    status = get_chat_status("00000000-0000-4000-8000-000000000099")

    assert status["state"] == "unknown"
    assert status["stage"] == "Unknown"
    assert status["attrs"] == {}
