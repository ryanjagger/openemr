#!/usr/bin/env python3
"""
Export Langfuse cost/usage metrics grouped by:
  environment, trace name, model

Outputs CSV columns:
  environment, trace_name, model, request_count, llm_call_count,
  total_cost_usd, input_tokens, output_tokens, total_tokens,
  latency_p50_ms, latency_p95_ms, error_count, error_rate

Auth:
  LANGFUSE_PUBLIC_KEY=<pk-lf-...>
  LANGFUSE_SECRET_KEY=<sk-lf-...>
  LANGFUSE_BASE_URL=https://us.cloud.langfuse.com   # or https://cloud.langfuse.com / self-hosted origin

Example:
  python export_langfuse_cost_metrics.py \
    --from-ts 2026-04-01T00:00:00Z \
    --to-ts 2026-05-01T00:00:00Z \
    --environment production \
    --out openemr_langfuse_cost_metrics.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_BASE_URL = "https://us.cloud.langfuse.com"
DEFAULT_ROW_LIMIT = 1000


@dataclass(frozen=True)
class Auth:
    public_key: str
    secret_key: str


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_base_url(raw: str) -> str:
    base = raw.rstrip("/")
    if base.endswith("/api/public"):
        base = base[: -len("/api/public")]
    return base


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def request_json(base_url: str, auth: Auth, path: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"{base_url}{path}?{urlencode(params)}"
    req = Request(url)
    # Langfuse Public API uses Basic Auth: username=public key, password=secret key.
    import base64

    token = base64.b64encode(f"{auth.public_key}:{auth.secret_key}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=60) as resp:  # noqa: S310 - user-provided API URL by design
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {path}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error calling {path}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {path}: {raw[:500]}") from exc


def langfuse_metrics(base_url: str, auth: Auth, *, v2: bool, query: dict[str, Any]) -> list[dict[str, Any]]:
    path = "/api/public/v2/metrics" if v2 else "/api/public/metrics"
    payload = request_json(base_url, auth, path, {"query": json.dumps(query, separators=(",", ":"))})
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected metrics response shape from {path}: {payload}")
    return [row for row in data if isinstance(row, dict)]


def string_filter(column: str, values: list[str]) -> dict[str, Any] | None:
    values = [v for v in values if v]
    if not values:
        return None
    if len(values) == 1:
        return {"column": column, "operator": "=", "value": values[0], "type": "string"}
    return {"column": column, "operator": "any of", "value": values, "type": "stringOptions"}


def array_filter(column: str, values: list[str]) -> dict[str, Any] | None:
    values = [v for v in values if v]
    if not values:
        return None
    return {"column": column, "operator": "all of", "value": values, "type": "arrayOptions"}


def compact_filters(filters: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    return [f for f in filters if f is not None]


def metric(row: dict[str, Any], measure: str, aggregation: str, default: float = 0.0) -> float:
    # Langfuse examples currently use measure_aggregation, while some historic outputs used aggregation_measure.
    candidates = [
        f"{measure}_{aggregation}",
        f"{aggregation}_{measure}",
        measure,
    ]
    for key in candidates:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def group_key(row: dict[str, Any]) -> tuple[str, str, str]:
    env = str(row.get("environment") or "")
    trace_name = str(row.get("traceName") or row.get("name") or "")
    model = str(row.get("providedModelName") or row.get("model") or "")
    return env, trace_name, model


def trace_key(row: dict[str, Any]) -> tuple[str, str]:
    env = str(row.get("environment") or "")
    trace_name = str(row.get("name") or row.get("traceName") or "")
    return env, trace_name


def build_generation_filters(args: argparse.Namespace) -> list[dict[str, Any]]:
    return compact_filters(
        [
            {"column": "type", "operator": "=", "value": "GENERATION", "type": "string"},
            string_filter("environment", args.environment),
            string_filter("traceName", args.trace_name),
            string_filter("providedModelName", args.model),
            array_filter("tags", args.tag),
        ]
    )


def build_trace_filters(args: argparse.Namespace) -> list[dict[str, Any]]:
    return compact_filters(
        [
            string_filter("environment", args.environment),
            string_filter("name", args.trace_name),
            array_filter("tags", args.tag),
        ]
    )


def query_main_metrics(base_url: str, auth: Auth, args: argparse.Namespace) -> list[dict[str, Any]]:
    query = {
        "view": "observations",
        "dimensions": [
            {"field": "environment"},
            {"field": "traceName"},
            {"field": "providedModelName"},
        ],
        "metrics": [
            {"measure": "count", "aggregation": "count"},
            {"measure": "totalCost", "aggregation": "sum"},
            {"measure": "inputTokens", "aggregation": "sum"},
            {"measure": "outputTokens", "aggregation": "sum"},
            {"measure": "totalTokens", "aggregation": "sum"},
            {"measure": "latency", "aggregation": "p50"},
            {"measure": "latency", "aggregation": "p95"},
        ],
        "filters": build_generation_filters(args),
        "fromTimestamp": args.from_ts,
        "toTimestamp": args.to_ts,
        # Langfuse v2 source schema uses config.row_limit; docs call this rowLimit.
        # If your tenant rejects this, remove the config block or set a lower --row-limit.
        "config": {"row_limit": args.row_limit},
    }
    return langfuse_metrics(base_url, auth, v2=True, query=query)


def query_error_counts(base_url: str, auth: Auth, args: argparse.Namespace) -> dict[tuple[str, str, str], int]:
    filters = build_generation_filters(args)
    filters.append({"column": "level", "operator": "=", "value": "ERROR", "type": "string"})
    query = {
        "view": "observations",
        "dimensions": [
            {"field": "environment"},
            {"field": "traceName"},
            {"field": "providedModelName"},
        ],
        "metrics": [{"measure": "count", "aggregation": "count"}],
        "filters": filters,
        "fromTimestamp": args.from_ts,
        "toTimestamp": args.to_ts,
        "config": {"row_limit": args.row_limit},
    }
    rows = langfuse_metrics(base_url, auth, v2=True, query=query)
    return {group_key(row): int(metric(row, "count", "count")) for row in rows}


def query_trace_counts(base_url: str, auth: Auth, args: argparse.Namespace) -> dict[tuple[str, str], int]:
    """Optional application-level request counts via legacy v1 trace view.

    v2 Metrics API intentionally has no traces view. The v1 traces view is still useful
    here because the cost-analysis CSV is grouped by trace name.
    """
    query = {
        "view": "traces",
        "dimensions": [{"field": "environment"}, {"field": "name"}],
        "metrics": [{"measure": "count", "aggregation": "count"}],
        "filters": build_trace_filters(args),
        "fromTimestamp": args.from_ts,
        "toTimestamp": args.to_ts,
    }
    rows = langfuse_metrics(base_url, auth, v2=False, query=query)
    return {trace_key(row): int(metric(row, "count", "count")) for row in rows}


def write_csv(
    rows: list[dict[str, Any]],
    error_counts: dict[tuple[str, str, str], int],
    trace_counts: dict[tuple[str, str], int],
    out_path: str,
) -> None:
    fieldnames = [
        "environment",
        "trace_name",
        "model",
        "request_count",
        "llm_call_count",
        "total_cost_usd",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_p50_ms",
        "latency_p95_ms",
        "error_count",
        "error_rate",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            env, trace_name, model = group_key(row)
            llm_count = int(metric(row, "count", "count"))
            err_count = error_counts.get((env, trace_name, model), 0)
            trace_count = trace_counts.get((env, trace_name))
            request_count = trace_count if trace_count is not None else llm_count
            error_rate = (err_count / llm_count) if llm_count else 0.0
            writer.writerow(
                {
                    "environment": env,
                    "trace_name": trace_name,
                    "model": model,
                    "request_count": request_count,
                    "llm_call_count": llm_count,
                    "total_cost_usd": f"{metric(row, 'totalCost', 'sum'):.8f}",
                    "input_tokens": int(metric(row, "inputTokens", "sum")),
                    "output_tokens": int(metric(row, "outputTokens", "sum")),
                    "total_tokens": int(metric(row, "totalTokens", "sum")),
                    "latency_p50_ms": f"{metric(row, 'latency', 'p50'):.2f}",
                    "latency_p95_ms": f"{metric(row, 'latency', 'p95'):.2f}",
                    "error_count": err_count,
                    "error_rate": f"{error_rate:.6f}",
                }
            )


def parse_args() -> argparse.Namespace:
    now = datetime.now(timezone.utc)
    default_to = iso_utc(now)
    default_from = iso_utc(now - timedelta(days=30))

    parser = argparse.ArgumentParser(description="Export grouped Langfuse AI cost metrics to CSV.")
    parser.add_argument("--from-ts", default=os.getenv("LANGFUSE_EXPORT_FROM_TS", default_from), help="ISO UTC start timestamp, e.g. 2026-04-01T00:00:00Z")
    parser.add_argument("--to-ts", default=os.getenv("LANGFUSE_EXPORT_TO_TS", default_to), help="ISO UTC end timestamp, e.g. 2026-05-01T00:00:00Z")
    parser.add_argument("--base-url", default=os.getenv("LANGFUSE_BASE_URL", DEFAULT_BASE_URL), help="Langfuse origin, not including /api/public")
    parser.add_argument("--out", default="openemr_langfuse_cost_metrics.csv", help="Output CSV path")
    parser.add_argument("--row-limit", type=int, default=DEFAULT_ROW_LIMIT, help="Max grouped rows to request from v2 metrics API")
    parser.add_argument("--environment", action="append", default=[], help="Filter environment; repeatable, e.g. --environment production")
    parser.add_argument("--trace-name", action="append", default=[], help="Filter trace name; repeatable")
    parser.add_argument("--model", action="append", default=[], help="Filter provided model name; repeatable")
    parser.add_argument("--tag", action="append", default=[], help="Require tag; repeatable")
    parser.add_argument("--skip-trace-counts", action="store_true", help="Do not call v1 trace metrics for application request counts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    auth = Auth(public_key=require_env("LANGFUSE_PUBLIC_KEY"), secret_key=require_env("LANGFUSE_SECRET_KEY"))
    base_url = normalize_base_url(args.base_url)

    rows = query_main_metrics(base_url, auth, args)
    error_counts = query_error_counts(base_url, auth, args)

    trace_counts: dict[tuple[str, str], int] = {}
    if not args.skip_trace_counts:
        try:
            trace_counts = query_trace_counts(base_url, auth, args)
        except RuntimeError as exc:
            print(f"Warning: trace-count query failed; request_count will equal llm_call_count. {exc}", file=sys.stderr)

    write_csv(rows, error_counts, trace_counts, args.out)
    print(f"Wrote {len(rows)} grouped rows to {args.out}")
    if not rows:
        print("No rows returned. Check time range, environment/trace filters, and whether Langfuse has generation observations in this period.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
