# OpenRouter Provider Plan

Date: 2026-05-02

## Context

The `oe-ai-agent` sidecar currently routes real LLM calls through Anthropic via
LiteLLM. The existing abstraction is already mostly provider-neutral:

- `oe-ai-agent/src/oe_ai_agent/llm/client.py` defines the `LlmClient` protocol.
- `oe-ai-agent/src/oe_ai_agent/llm/litellm_client.py` calls
  `litellm.acompletion()`.
- `oe-ai-agent/src/oe_ai_agent/main.py` currently only accepts
  `LLM_PROVIDER=mock` or `LLM_PROVIDER=anthropic`.

OpenRouter is OpenAI-compatible at the API surface and is supported by LiteLLM
with model names prefixed as `openrouter/<openrouter-model-id>`.

## Goal

Add OpenRouter as an alternative provider for synthetic/demo and eval usage,
without changing the agent graph, verifier, PHP module, or FHIR data-access
model.

## Proposed Runtime Configuration

```bash
LLM_PROVIDER=openrouter
LLM_MODEL=openrouter/<openrouter-model-id>
OPENROUTER_API_KEY=...
OPENROUTER_REQUIRE_ZDR=true
OPENROUTER_DATA_COLLECTION=deny
OPENROUTER_ALLOW_FALLBACKS=false
OPENROUTER_REQUIRE_PARAMETERS=true
OPENROUTER_SITE_URL=https://example.local      # optional
OPENROUTER_APP_TITLE=OpenEMR AI Agent          # optional
```

Important naming detail: OpenRouter's direct API model IDs look like
`openai/gpt-5.2`, but LiteLLM expects `openrouter/openai/gpt-5.2`.

## Implementation Steps

1. Extend `Settings` in `oe-ai-agent/src/oe_ai_agent/config.py` with:
   - `openrouter_api_key: str | None`
   - `openrouter_require_zdr: bool`
   - `openrouter_data_collection: str`
   - `openrouter_allow_fallbacks: bool`
   - `openrouter_require_parameters: bool`
   - optional app attribution fields.

2. Add an `openrouter` branch in `_llm_client()` in
   `oe-ai-agent/src/oe_ai_agent/main.py`.
   - Require `OPENROUTER_API_KEY`.
   - Require `LLM_MODEL` to start with `openrouter/` when
     `LLM_PROVIDER=openrouter`.
   - Instantiate the same `LiteLLMClient`.

3. Update `LiteLLMClient` to accept provider-specific request options.
   - Add an optional `extra_kwargs: dict[str, Any]`.
   - Merge those into `_base_kwargs()`.
   - For OpenRouter, pass:

```python
provider={
    "zdr": True,
    "data_collection": "deny",
    "require_parameters": True,
    "allow_fallbacks": False,
}
```

4. Keep routing deterministic by default.
   - Start with `allow_fallbacks=false`.
   - If fallback routing is later enabled, update audit metadata so the actual
     upstream provider/model can be inspected after a request.

5. Update `docker/development-easy/docker-compose.yml`.
   - Add the OpenRouter environment variables to the `oe-ai-agent` service.
   - Keep `LLM_PROVIDER` defaulting to `mock` for local safety.

6. Update the eval harness.
   - Add `openrouter` to `--provider` choices in `oe-ai-agent/evals/run_eval.py`.
   - Use `OPENROUTER_API_KEY` when provider is `openrouter`.
   - Run the existing synthetic fixtures against the exact chosen model.

7. Add tests.
   - Config parsing for OpenRouter env vars.
   - `_llm_client()` behavior for missing API key and invalid model prefix.
   - `LiteLLMClient` propagation of OpenRouter `provider` options.

## Verification Checklist

Run local deterministic tests:

```bash
cd oe-ai-agent
uv run pytest
```

Run synthetic live evals:

```bash
cd oe-ai-agent
export OPENROUTER_API_KEY=...
uv run python evals/run_eval.py \
  --label openrouter-baseline \
  --provider openrouter \
  --model openrouter/<openrouter-model-id>
```

Compare against the Anthropic baseline:

- verified item count
- parse failures
- verifier drop rules
- chat tool-call behavior
- latency
- cost

## Compliance Notes

Treat OpenRouter as synthetic/demo only until legal and compliance review confirms
whether the intended deployment has adequate BAA coverage.

OpenRouter documentation says:

- Its API request/response schema is similar to OpenAI Chat Completions.
- It supports `tools` and `response_format`.
- It does not store prompts or responses unless logging/product-improvement
  settings are enabled.
- It stores request metadata such as token counts and latency.
- Underlying providers have their own data-handling policies.
- Per-request Zero Data Retention routing can be requested with
  `provider.zdr=true`.

ZDR and "no training" controls are useful, but they are not the same thing as a
HIPAA Business Associate Agreement.

## References

- OpenRouter API overview:
  https://openrouter.ai/docs/api/reference/overview
- OpenRouter authentication:
  https://openrouter.ai/docs/api/reference/authentication
- OpenRouter data collection:
  https://openrouter.ai/docs/guides/privacy/data-collection
- OpenRouter provider logging:
  https://openrouter.ai/docs/guides/privacy/provider-logging
- OpenRouter ZDR:
  https://openrouter.ai/docs/guides/features/zdr
- LiteLLM getting started:
  https://docs.litellm.ai/
