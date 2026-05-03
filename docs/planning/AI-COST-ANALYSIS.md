# OpenEMR AI Agent Cost Analysis

Date: 2026-05-02
Scope: AI-related spend for the OpenEMR `oe-ai-agent` sidecar.

## Summary

The current measured AI data set is small: the Langfuse export shows 89 LLM
calls, 425,528 total tokens, and $1.66 of model cost. The observed average is
$0.0186 per AI invocation on `anthropic/claude-sonnet-4-6`. This is useful as
a first baseline, but it is not yet production telemetry.

For planning, use active physicians, not registered users or patients. The
working unit is one physician seeing 20 patients/day. Assuming 20 clinic
days/month, that is 400 patient encounters/month. If every encounter generates
one brief and three chat turns, and a chat turn averages 1.5 LLM calls because
some turns use the tool loop, one physician produces about 2,200 LLM-equivalent
calls/month. Under the current Sonnet model mix and prompt shape, that is about
$41/month in model cost per physician before infrastructure and observability.
At 100 / 1K / 10K / 100K active physicians, the planning model is roughly
$4.1K / $41K / $410K / $4.1M per month in model cost.

The main scale risk is not token price alone. The current architecture is a
synchronous PHP request to a FastAPI sidecar, with an in-memory conversation
cache and no durable queue. That is acceptable for demo and small pilots, but
the 1K-physician tier should introduce Redis-backed state, worker queues,
per-physician rate limits, and prompt caching. At 10K+ physicians, provider
throughput contracts, autoscaled sidecar workers, observability sampling, cost
budgets, and a durable audit pipeline become mandatory.

## Observed Dev Spend

Source: [`langfuse-metrics-export`](tools/langfuse/langfuse-metrics-export) |

| Environment | LLM calls | Cost | Tokens | Avg cost/call |
|---|---:|---:|---:|---:|
| ALL | 89 | $1.6570 | 425,528 | $0.0186 |

Observed token mix: 393,829 input tokens and 31,699 output tokens. The cost
matches Sonnet pricing at $3 per million input tokens and $15 per million
output tokens. Treat this as captured AI API dev spend, not full development
spend. If the goal is all-in dev spend, add Anthropic/OpenRouter invoices,
Claude Code subscriptions/API usage, Railway usage, Langfuse plan charges, and
human time.

## Projection Model

Planning assumptions:

- 1 scale unit = 1 active physician.
- 20 patients/physician/day and 20 clinic days/month.
- 1 patient brief per patient.
- 3 visible chat turns per patient in the planning case.
- 1 brief = 1 LLM call.
- 1 visible chat turn = 1.5 LLM calls on average, allowing for tool-loop turns.
- Current blended cost = $0.0186 per LLM call from observed Langfuse data.
- Infrastructure ranges include incremental AI sidecar, queue/cache, extra app
  capacity, and observability, but not a full enterprise support/compliance
  program.
- Full trace capture is assumed at 100 and 1K physicians;
  sampling/retention controls are assumed at 10K and 100K.

Per-physician workload:

| Metric | Value |
|---|---:|
| Patients/month | 400 |
| Briefs/month | 400 |
| Chat turns/month | 1,200 |
| LLM-equivalent calls/month | 2,200 |
| Model cost/month | $41 |
| Model cost/patient encounter | $0.10 |

Scaled planning case:

| Active physicians | Patient encounters/mo | LLM-equivalent calls/mo | Model cost/mo | Infra + observability/mo | Planning run-rate | Architecture posture |
|---:|---:|---:|---:|---:|---|
| 100 | 40,000 | 220,000 | $4.1K | $0.5K-$1.5K | $4.6K-$5.6K | Current sync path can work for a pilot, but add per-physician limits, model budget alarms, and a BAA/data policy before PHI. |
| 1,000 | 400,000 | 2,200,000 | $41K | $5K-$15K | $46K-$56K | Add Redis-backed conversation state, queue/polling for brief generation, horizontal sidecar workers, prompt caching, and rate limiting. |
| 10,000 | 4,000,000 | 22,000,000 | $410K | $45K-$150K | $455K-$560K | Autoscale workers, split web/worker roles, tune FHIR reads, introduce model routing, sample Langfuse traces, and enforce tenant/physician cost budgets. |
| 100,000 | 40,000,000 | 220,000,000 | $4.1M | $350K-$1.2M+ | $4.5M-$5.3M+ | Dedicated provider capacity, queue-first architecture, multi-region/DR planning, external append-only audit/SIEM, aggressive caching, and formal FinOps controls. |

Sensitivity for one physician: 2 chat turns/patient at 1.25 LLM calls/turn is
about $26/month. 5 chat turns/patient at 2 LLM calls/turn is about $82/month.
This means the 100-physician tier ranges from roughly $2.6K-$8.2K/month in
model cost before caching, model routing, infrastructure, and observability.

## Cost Levers

1. Prompt caching. Anthropic prompt caching is not yet enabled. Once enabled,
repeated chat turns can be priced at 10% of normal input cost.

2. Model routing. Currently using Anthropic Sonnet in MVP demo. A mix of models
for different functionality (routing, classification, summaries) would add cost
efficiency.

3. Caching and deduplication. Cache per-patient context for the current session,
avoid regenerating identical briefs on repeated chart opens, and precompute only
when the appointment schedule justifies it.

4. Observability volume. Full Langfuse traces are valuable during pilot. At
10K+ physicians, retain full traces for errors, samples, eval cohorts, and
audited incidents; aggregate cost/latency metrics for the rest.

## Architecture Recommendations by Tier (Generated Recommendations)

At 100 physicians, keep the current FastAPI sidecar and synchronous UI path, but
add budget alerts, per-physician throttles, and a clear PHI policy. The current
30-second PHP sidecar timeout leaves little room if p95 remains near 18 seconds.

At 1K physicians, make Redis mandatory. The sidecar's in-memory conversation
store has a 256-entry default cap and cannot support horizontal scaling safely.
Move conversation state and job results to Redis, put brief generation behind a
queue, and let the browser poll or stream completion.

At 10K physicians, treat the AI agent as its own service tier. Run separate web
and worker pools, shape concurrency to provider rate limits, add canary model
rollouts, and track cost by tenant, physician, action, model, and patient-context
size. Use Langfuse primarily for sampled traces and failures.

At 100K physicians, negotiate provider capacity and compliance terms directly. A
gateway layer should enforce routing, budgets, data residency, retries, and
fallback policy. Audit logs should flow to an external tamper-evident store, not
only the OpenEMR database.
