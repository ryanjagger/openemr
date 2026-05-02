"""Shared helpers for live eval runners."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx
import respx

DEFAULT_OUTPUT_DIR = Path(__file__).parent / "runs"
EVAL_FHIR_BASE = "http://eval-fhir.local/apis/default/fhir"
EVAL_PATIENT_UUID = "eval-patient-fixture"

_DATE_TOKEN = re.compile(r"\{\{TODAY([+-]\d+)D\}\}")


class HasRule(Protocol):
    rule: str


class HasValueType(Protocol):
    @property
    def value(self) -> str: ...


class HasType(Protocol):
    type: HasValueType


def default_output_path(label: str, *, prefix: str = "") -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{prefix}{safe}.jsonl"
    return DEFAULT_OUTPUT_DIR / name


def load_fixtures(
    fixtures_dir: Path,
    only: list[str] | None = None,
) -> list[dict[str, Any]]:
    paths = sorted(fixtures_dir.glob("*.json"))
    selected: list[dict[str, Any]] = []
    for path in paths:
        if only and not any(token in path.stem for token in only):
            continue
        raw = path.read_text(encoding="utf-8")
        rendered = substitute_date_tokens(raw)
        data = json.loads(rendered)
        data["__id__"] = path.stem
        selected.append(data)
    return selected


def substitute_date_tokens(text: str) -> str:
    today = datetime.now(tz=UTC).date()

    def replace(match: re.Match[str]) -> str:
        offset_days = int(match.group(1))
        return (today + timedelta(days=offset_days)).isoformat()

    return _DATE_TOKEN.sub(replace, text)


def install_fhir_routes(
    router: respx.MockRouter,
    fhir: dict[str, Any],
    *,
    fhir_base_url: str = EVAL_FHIR_BASE,
    patient_uuid: str = EVAL_PATIENT_UUID,
) -> None:
    for resource_type, body in fhir.items():
        if resource_type == "Patient":
            router.get(f"{fhir_base_url}/Patient/{patient_uuid}").mock(
                return_value=httpx.Response(200, json=body),
            )
            continue
        router.get(f"{fhir_base_url}/{resource_type}").mock(
            return_value=httpx.Response(200, json=body),
        )


def drop_counts(failures: Sequence[HasRule]) -> dict[str, int]:
    counter = Counter(f.rule for f in failures)
    return dict(counter)


def type_counts(items: Sequence[HasType]) -> dict[str, int]:
    counter = Counter(item.type.value for item in items)
    return dict(counter)
