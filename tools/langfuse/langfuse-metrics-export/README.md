# Langfuse metrics export for OpenEMR AI cost analysis

This export is designed to produce the CSV needed for the cost-analysis deliverable:

```text
environment, trace_name, model, request_count, llm_call_count,
total_cost_usd, input_tokens, output_tokens, total_tokens,
latency_p50_ms, latency_p95_ms, error_count, error_rate
```

## 1. Set credentials

```bash
export LANGFUSE_PUBLIC_KEY='pk-lf-...'
export LANGFUSE_SECRET_KEY='sk-lf-...'
export LANGFUSE_BASE_URL='https://us.cloud.langfuse.com'
```

Use `https://cloud.langfuse.com` for EU, `https://jp.cloud.langfuse.com` for Japan, or your self-hosted origin. Do not include `/api/public` in `LANGFUSE_BASE_URL`.

## 2. Run a monthly export

```bash
python export_langfuse_cost_metrics.py \
  --from-ts 2026-04-01T00:00:00Z \
  --to-ts 2026-05-03T00:00:00Z \
  --out openemr_langfuse_cost_metrics_2026_04.csv
```

Optional filters:

```bash
python export_langfuse_cost_metrics.py \
  --from-ts 2026-04-01T00:00:00Z \
  --to-ts 2026-05-01T00:00:00Z \
  --environment production \
  --trace-name patient-brief \
  --out openemr_langfuse_cost_metrics_production.csv
```

## Notes

- `llm_call_count` is the count of Langfuse `GENERATION` observations grouped by environment, trace name, and model.
- `request_count` comes from the legacy v1 trace metrics view grouped by environment and trace name, then repeated across model rows. If the trace-count query fails, the script falls back to `request_count = llm_call_count`.
- `error_rate = ERROR generation observations / generation observations`. This is only meaningful if the app marks failed observations with `level="ERROR"`.
- The OpenEMR AI agent currently sends usage keys as `prompt_tokens`, `completion_tokens`, and `total_tokens`; Langfuse v2 metrics normalizes these into `inputTokens`, `outputTokens`, and `totalTokens` when model/usage definitions are available.
