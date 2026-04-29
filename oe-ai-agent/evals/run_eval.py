"""Eval runner for the patient-brief agent.

Calls the live LLM against fixtures with FHIR responses mocked via respx.
Writes one JSONL row per fixture so prompt/model A/B comparisons are
greppable. Not a CI test — LLM output is non-deterministic and the runner
costs API credits.

Usage::

    export ANTHROPIC_API_KEY=sk-ant-...
    uv run python evals/run_eval.py --label baseline

Fixtures live in ``evals/fixtures/*.json``. Each fixture's ``fhir`` map
contains FHIR responses keyed by either ``Patient`` (returned for the
``Patient/{uuid}`` read) or any other resource type (returned for
``GET /{ResourceType}*`` searches). Date tokens of the form
``{{TODAY-15D}}`` are substituted at load time so fixtures stay evergreen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import respx

from oe_ai_agent.agent.graph import build_graph
from oe_ai_agent.agent.state import AgentState
from oe_ai_agent.config import FREETEXT_ITEM_TYPES
from oe_ai_agent.llm import LiteLLMClient, LlmClient, MockLlmClient
from oe_ai_agent.schemas.brief import BriefItem, BriefItemType, VerificationFailure

DEFAULT_FIXTURES_DIR = Path(__file__).parent / "fixtures"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "runs"
EVAL_FHIR_BASE = "http://eval-fhir.local/apis/default/fhir"
EVAL_PATIENT_UUID = "eval-patient-fixture"

_DATE_TOKEN = re.compile(r"\{\{TODAY([+-]\d+)D\}\}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--label", required=True, help="Free-form label for this run")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--provider",
        choices=["anthropic", "mock"],
        default="anthropic",
        help="`mock` is for harness debugging only — not a real eval",
    )
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6")
    parser.add_argument(
        "--enable-freetext-types",
        action="store_true",
        help="Allow recent_event/agenda_item items (off by default per T3.10)",
    )
    parser.add_argument(
        "--only",
        action="append",
        help="Run only fixtures whose stem matches one of these (repeatable)",
    )
    args = parser.parse_args()

    fixtures = _load_fixtures(args.fixtures, only=args.only)
    if not fixtures:
        print(f"no fixtures found under {args.fixtures}", file=sys.stderr)
        return 1

    llm = _build_llm(args.provider, args.model)
    allowed_types = _allowed_types(args.enable_freetext_types)

    output_path = args.output or _default_output_path(args.label)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"running {len(fixtures)} fixture(s) | model={llm.model_id} | label={args.label}",
        file=sys.stderr,
    )
    print(f"writing → {output_path}", file=sys.stderr)

    started = time.perf_counter()
    summary = Counter[str]()
    with output_path.open("w", encoding="utf-8") as out:
        for fixture in fixtures:
            row = asyncio.run(_run_one(fixture, llm, allowed_types, args.label))
            out.write(json.dumps(row) + "\n")
            out.flush()
            summary["fixtures"] += 1
            summary["items_verified"] += row["items_verified"]
            failed = not all(row["expectations_met"].values())
            if failed and row["known_limitation"]:
                summary["known_limitation_failures"] += 1
            elif failed:
                summary["fixtures_with_failed_expectations"] += 1
            mark = " (known limitation)" if row["known_limitation"] else ""
            print(
                f"  {row['fixture_id']}: {row['items_verified']} verified "
                f"({row['duration_ms']} ms){mark}",
                file=sys.stderr,
            )

    elapsed = time.perf_counter() - started
    print(
        f"done in {elapsed:.1f}s — "
        f"{summary['items_verified']} items across {summary['fixtures']} fixtures, "
        f"{summary['fixtures_with_failed_expectations']} failed expectations "
        f"({summary['known_limitation_failures']} known limitations)",
        file=sys.stderr,
    )
    return 0


def _build_llm(provider: str, model: str) -> LlmClient:
    if provider == "mock":
        return MockLlmClient.synthesizing()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY is required for --provider anthropic. "
            "Use --provider mock to debug the harness without it.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return LiteLLMClient(model=model, api_key=api_key)


def _allowed_types(enable_freetext: bool) -> frozenset[BriefItemType]:
    if enable_freetext:
        return frozenset(BriefItemType)
    return frozenset(BriefItemType) - FREETEXT_ITEM_TYPES


def _default_output_path(label: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"{stamp}_{safe}.jsonl"


def _load_fixtures(
    fixtures_dir: Path,
    only: list[str] | None = None,
) -> list[dict[str, Any]]:
    paths = sorted(fixtures_dir.glob("*.json"))
    selected: list[dict[str, Any]] = []
    for path in paths:
        if only and not any(token in path.stem for token in only):
            continue
        raw = path.read_text(encoding="utf-8")
        rendered = _substitute_date_tokens(raw)
        data = json.loads(rendered)
        data["__id__"] = path.stem
        selected.append(data)
    return selected


def _substitute_date_tokens(text: str) -> str:
    today = datetime.now(tz=UTC).date()

    def replace(match: re.Match[str]) -> str:
        offset_days = int(match.group(1))
        return (today + timedelta(days=offset_days)).isoformat()

    return _DATE_TOKEN.sub(replace, text)


async def _run_one(
    fixture: dict[str, Any],
    llm: LlmClient,
    allowed_types: frozenset[BriefItemType],
    label: str,
) -> dict[str, Any]:
    fixture_id = fixture["__id__"]
    fixture_label = fixture.get("label", "")
    expectations = fixture.get("expectations", {})
    known_limitation = bool(fixture.get("known_limitation", False))
    started = time.perf_counter()

    parsed_items: list[BriefItem] = []
    verified_items: list[BriefItem] = []
    failures: list[VerificationFailure] = []
    error: str | None = None

    try:
        # assert_all_mocked=False lets unrelated HTTP calls (e.g. LiteLLM's
        # lazy GitHub fetch of beta-headers config, and any raw provider API
        # traffic that bypasses the cached client) reach the real network
        # instead of raising. Only the FHIR base is mocked.
        with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
            _install_fhir_routes(router, fixture["fhir"])
            graph = build_graph(llm, allowed_types=allowed_types)
            final = await graph.ainvoke(  # type: ignore[attr-defined]
                AgentState(
                    patient_uuid=EVAL_PATIENT_UUID,
                    fhir_base_url=EVAL_FHIR_BASE,
                    bearer_token="eval-bearer-stub",
                    request_id=f"eval-{fixture_id}",
                ),
            )
        state = AgentState.model_validate(final)
        parsed_items = state.parsed_items
        verified_items = state.verified_items
        failures = state.verification_failures
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.perf_counter() - started) * 1000)

    return {
        "ts": datetime.now(tz=UTC).isoformat(),
        "label": label,
        "model_id": llm.model_id,
        "fixture_id": fixture_id,
        "fixture_label": fixture_label,
        "known_limitation": known_limitation,
        "error": error,
        "items_emitted": len(parsed_items),
        "items_verified": len(verified_items),
        "drop_count_by_rule": _drop_counts(failures),
        "type_counts_verified": _type_counts(verified_items),
        "duration_ms": duration_ms,
        "expectations_met": _check_expectations(verified_items, failures, expectations),
        "items": [_item_to_dict(i) for i in verified_items],
        "failures": [f.model_dump() for f in failures],
    }


def _install_fhir_routes(router: respx.MockRouter, fhir: dict[str, Any]) -> None:
    for resource_type, body in fhir.items():
        if resource_type == "Patient":
            router.get(f"{EVAL_FHIR_BASE}/Patient/{EVAL_PATIENT_UUID}").mock(
                return_value=httpx.Response(200, json=body),
            )
            continue
        router.get(f"{EVAL_FHIR_BASE}/{resource_type}").mock(
            return_value=httpx.Response(200, json=body),
        )


def _drop_counts(failures: list[VerificationFailure]) -> dict[str, int]:
    counter = Counter(f.rule for f in failures)
    return dict(counter)


def _type_counts(items: list[BriefItem]) -> dict[str, int]:
    counter = Counter(item.type.value for item in items)
    return dict(counter)


def _item_to_dict(item: BriefItem) -> dict[str, Any]:
    return {
        "type": item.type.value,
        "text": item.text,
        "verbatim_excerpts": list(item.verbatim_excerpts),
        "citations": [
            {"resource_type": c.resource_type, "resource_id": c.resource_id}
            for c in item.citations
        ],
    }


def _check_expectations(
    items: list[BriefItem],
    failures: list[VerificationFailure],
    expectations: dict[str, Any],
) -> dict[str, bool]:
    types_present = {i.type.value for i in items}
    cited_ids = {c.resource_id for item in items for c in item.citations}
    text_blob = " ".join(item.text for item in items).lower()
    drop_counts = Counter(f.rule for f in failures)
    result: dict[str, bool] = {}

    if "min_verified_items" in expectations:
        result["min_verified_items"] = len(items) >= int(expectations["min_verified_items"])
    if "max_verified_items" in expectations:
        result["max_verified_items"] = len(items) <= int(expectations["max_verified_items"])
    if "expected_types_present" in expectations:
        wanted = set(expectations["expected_types_present"])
        result["expected_types_present"] = wanted.issubset(types_present)
    if "expected_types_absent" in expectations:
        unwanted = set(expectations["expected_types_absent"])
        result["expected_types_absent"] = unwanted.isdisjoint(types_present)
    if "expected_citations" in expectations:
        wanted_ids = set(expectations["expected_citations"])
        result["expected_citations"] = wanted_ids.issubset(cited_ids)
    if "forbidden_citations" in expectations:
        unwanted_ids = set(expectations["forbidden_citations"])
        result["forbidden_citations"] = unwanted_ids.isdisjoint(cited_ids)
    if "must_contain" in expectations:
        needles = [str(s).lower() for s in expectations["must_contain"]]
        result["must_contain"] = all(needle in text_blob for needle in needles)
    if "must_not_contain" in expectations:
        forbidden = [str(s).lower() for s in expectations["must_not_contain"]]
        result["must_not_contain"] = all(needle not in text_blob for needle in forbidden)
    if "expected_drop_rules" in expectations:
        wanted_drops: dict[str, int] = expectations["expected_drop_rules"]
        result["expected_drop_rules"] = all(
            drop_counts.get(rule, 0) >= int(count) for rule, count in wanted_drops.items()
        )

    return result


if __name__ == "__main__":
    raise SystemExit(main())
