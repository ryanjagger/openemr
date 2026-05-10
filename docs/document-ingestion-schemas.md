# Document Ingestion Schemas

Reference for the typed contracts that flow through the document ingestion
pipeline. There are two schema layers, one per language:

- **Python sidecar** — Pydantic v2 models in
  `oe-ai-agent/src/oe_ai_agent/schemas/`. These are the wire contracts that
  cross the HTTP boundary and the strict response format the LLM is forced
  into.
- **PHP module** — `final readonly` DTO classes in
  `interface/modules/custom_modules/oe-module-ai-agent/src/DTO/`. These are
  the typed shapes inside OpenEMR (the front end uses plain JS),
  and DTOs replace untyped associative arrays at every internal boundary.

The two layers are intentionally not generated from a shared spec. They
agree by convention on field names (snake_case on the wire, camelCase in
PHP property names) and by the static `fromExtraction()` adapters on each
PHP DTO that parse the sidecar JSON defensively.

```
            sidecar JSON (snake_case)                         PHP DTOs (camelCase)
Pydantic ────────────────────────────────▶ HTTP ──▶ DTO::fromExtraction(...)
DocumentExtractionResponse                          LabIngestionRequest / IntakeIngestionRequest
```

## Python (Pydantic v2)

All models use `model_config = ConfigDict(frozen=True)` — they are
immutable, hashable, and safe to share between async tasks. Source:
[`oe-ai-agent/src/oe_ai_agent/schemas/document_extraction.py`](oe-ai-agent/src/oe_ai_agent/schemas/document_extraction.py).

### Type aliases

```python
DocumentType    = Literal["lab_report", "intake_form"]
IntakeAnswerType = Literal["string", "boolean", "choice", "integer", "decimal", "date"]
BboxSource      = Literal["text_layer", "ocr", "llm"]
BboxTarget      = Literal["row", "value", "field", "snippet"]
```

These are the only allowed values. Anything else is a 422 from FastAPI's
Pydantic validator before user code runs.

### `DocumentExtractionRequest`

The sidecar's input — sent by `DocumentIngestionWorker` (PHP) to
`POST /v1/documents/extract`.

| Field             | Type            | Notes                                              |
|-------------------|-----------------|----------------------------------------------------|
| `request_id`      | `str`           | UUID minted by the PHP worker per document.       |
| `document_uuid`   | `str`           | OpenEMR `Document` UUID.                           |
| `document_type`   | `DocumentType`  | `lab_report` or `intake_form`.                     |
| `filename`        | `str`           | Original upload filename.                          |
| `mime_type`       | `str`           | MIME from the `Document` record.                   |
| `content_base64`  | `str`           | Base64 of `Document::get_data()` bytes.            |

### `SourceSnippet`

One quoted span from the source document, used for citation and click-through
to the PDF preview.

| Field             | Type                       | Notes                                                                          |
|-------------------|----------------------------|--------------------------------------------------------------------------------|
| `page_number`     | `int \| None`              | 1-based when present.                                                          |
| `text`            | `str`                      | Verbatim quote from the document.                                              |
| `bbox`            | `dict[str, float] \| None` | `{x, y, width, height}` in normalized page coords.                             |
| `bbox_source`     | `BboxSource \| None`       | Set by the deterministic localizer post-LLM. `None` = never tried/found.       |
| `bbox_confidence` | `float \| None`            | 0..1 from the localizer.                                                       |
| `bbox_target`     | `BboxTarget \| None`       | What the bbox encloses — table row, single value, form field, or bare snippet. |

### `ExtractedDocumentFact`

The unit of extraction. Lab results and intake answers share one row type;
the PHP side filters by `fact_type` on the way in.

| Field                  | Type                          | Lab? | Intake? | Notes                                                                                  |
|------------------------|-------------------------------|------|---------|----------------------------------------------------------------------------------------|
| `fact_type`            | `str`                         | yes  | yes     | `lab_result` or `intake_answer`. Open string by design — Tier 3 may add more.          |
| `label`                | `str \| None`                 | yes  | —       | Lab analyte name (e.g., "Hemoglobin A1c").                                             |
| `value_text`           | `str \| None`                 | yes  | —       | Free-form value when not numeric.                                                       |
| `value_numeric`        | `float \| None`               | yes  | —       |                                                                                          |
| `unit`                 | `str \| None`                 | yes  | —       |                                                                                          |
| `observed_on`          | `str \| None`                 | yes  | —       | ISO date string when extractable.                                                       |
| `reference_range`      | `str \| None`                 | yes  | —       | Verbatim from report (e.g., "3.5–5.0").                                                |
| `flag`                 | `str \| None`                 | yes  | —       | "H" / "L" / "Critical" etc.                                                             |
| `question`             | `str \| None`                 | —    | yes     | Form question text.                                                                     |
| `answer`               | `str \| None`                 | —    | yes     | User's answer.                                                                           |
| `link_id`              | `str \| None`                 | —    | yes     | Stable join key between FHIR `Questionnaire` and `QuestionnaireResponse`.               |
| `answer_type`          | `IntakeAnswerType \| None`    | —    | yes     | Picks the FHIR item type.                                                                |
| `answer_options`       | `list[str] \| None`           | —    | yes     | Choices when `answer_type == "choice"`.                                                  |
| `source_snippets`      | `list[SourceSnippet]`         | yes  | yes     | Required for citation. Defaults to `[]` but the verifier requires at least one in prod. |

### `DocumentExtractionResponse`

The sidecar's output. The PHP worker hashes this and the request, persists
both hashes in the audit log, then routes the body by `document_type`.

| Field                   | Type                              | Notes                                                  |
|-------------------------|-----------------------------------|--------------------------------------------------------|
| `request_id`            | `str`                             | Echo of the request.                                   |
| `model_id`              | `str`                             | LiteLLM model id, or `"mock"` when no provider wired. |
| `document_uuid`         | `str`                             | Echo.                                                  |
| `document_type`         | `DocumentType`                    | Echo.                                                  |
| `document_summary`      | `str \| None`                     | One-paragraph human summary, optional.                |
| `extraction_confidence` | `float \| None`                   | 0..1 self-reported by the LLM.                         |
| `facts`                 | `list[ExtractedDocumentFact]`     | Possibly empty — empty is a valid extraction.         |
| `meta`                  | `ResponseMeta`                    | Tokens, cost, latency, step trace.                    |

`model_dump_json_safe()` is the canonical serializer used by the FastAPI
handler — keeps Langfuse and the audit log on the same wire format.

### `ResponseMeta` / `UsageBlock` / `StepEntry`

Source: [`oe-ai-agent/src/oe_ai_agent/schemas/observability.py`](oe-ai-agent/src/oe_ai_agent/schemas/observability.py).

```python
class UsageBlock(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms_total: int = 0

class StepEntry(BaseModel):
    name: str
    duration_ms: int = 0
    status: Literal["ok", "error"] = "ok"
    error: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)

class ResponseMeta(BaseModel):
    usage: UsageBlock = Field(default_factory=UsageBlock)
    steps: list[StepEntry] = Field(default_factory=list)
```

`cost_usd` is best-effort — `litellm.completion_cost()` returns `0.0` for
models it can't price; that's expected, not an error.

### `UnindexedDocument`

The narrow manifest the supervisor sees in its routing prompt — used to
decide whether the `extractor` worker has anything to do for this turn.
Source: [`schemas/unindexed_document.py`](oe-ai-agent/src/oe_ai_agent/schemas/unindexed_document.py).

| Field                      | Type           | Notes                                                                      |
|----------------------------|----------------|----------------------------------------------------------------------------|
| `document_id`              | `int`          | OpenEMR pid.                                                                |
| `document_uuid`            | `str`          |                                                                              |
| `filename`                 | `str`          | Already visible to the user in the picker — not extra PHI exposure.        |
| `mimetype`                 | `str`          |                                                                              |
| `docdate`                  | `str \| None`  |                                                                              |
| `category_name`            | `str \| None`  | OpenEMR document category.                                                  |
| `inferred_document_type`   | `str \| None`  | Heuristic guess at `lab_report` / `intake_form` from category + filename.   |

### `PdfPagePreviewRequest` / `PdfPreviewBbox`

Used by `POST /v1/documents/pdf-page-preview` to render the click-through
preview the chat panel shows when a citation is clicked. Source:
[`schemas/pdf_preview.py`](oe-ai-agent/src/oe_ai_agent/schemas/pdf_preview.py).

```python
class PdfPreviewBbox(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)

class PdfPagePreviewRequest(BaseModel):
    content_base64: str
    page: int = Field(default=1, ge=1)
    bbox: PdfPreviewBbox | None = None
    bbox_unit: Literal["normalized", "percent", "pixels"] = "normalized"
```

These use field constraints (`ge=0`, `gt=0`, `ge=1`) instead of leaving
validation to handler code — the parse-don't-validate pattern from the root
CLAUDE.md.

## PHP (DTOs)

All DTOs are `final readonly` (immutable, no setters). Sources under
`interface/modules/custom_modules/oe-module-ai-agent/src/DTO/`.

### `LabIngestionRequest`

Aggregate input to `AiLabIngestionService::ingest()`.

| Field                   | Type                | Source                                                |
|-------------------------|---------------------|-------------------------------------------------------|
| `jobId`                 | `int`               | `ai_document_ingestion_jobs.id`                       |
| `documentId`            | `int`               | `ai_document_ingestion_documents.document_id`         |
| `documentUuid`          | `string`            | `ai_document_ingestion_documents.document_uuid`       |
| `patientId`             | `int`               | Job row.                                               |
| `userId`                | `int`               | Job row.                                               |
| `modelId`               | `string`            | Sidecar response, defaulting to `"unknown"`.          |
| `extractionConfidence`  | `?float`            | Sidecar response.                                      |
| `facts`                 | `list<LabFact>`     | Filtered to `fact_type === 'lab_result'`.             |

`fromExtraction(array $job, array $jobDocument, array $extraction): self`
performs the defensive parse — non-array inputs and wrong `fact_type`
values are dropped silently rather than throwing, so a partially-malformed
sidecar response still ingests its valid rows.

### `LabFact`

| Field             | Type                        | Notes                                                                       |
|-------------------|-----------------------------|------------------------------------------------------------------------------|
| `label`           | `string`                    | Falls back to `"AI extracted result"` if the LLM omits one.                  |
| `valueText`       | `?string`                   | Whitespace-only strings normalize to `null`.                                 |
| `valueNumeric`    | `?float`                    |                                                                              |
| `unit`            | `?string`                   |                                                                              |
| `referenceRange`  | `?string`                   |                                                                              |
| `flag`            | `?string`                   |                                                                              |
| `observedOn`      | `?string`                   | Sidecar emits ISO; PHP keeps as string and the lab service parses on insert. |
| `sourceSnippets`  | `list<LabSourceSnippet>`    |                                                                              |

### `LabSourceSnippet`

Mirrors the Pydantic `SourceSnippet`, with the bbox enums materialized as
class constants:

```php
public const BBOX_SOURCES = ['text_layer', 'ocr', 'llm'];
public const BBOX_TARGETS = ['row', 'value', 'field', 'snippet'];
```

| Field             | Type                        | Notes                                                                      |
|-------------------|-----------------------------|----------------------------------------------------------------------------|
| `pageNumber`      | `?int`                      |                                                                            |
| `text`            | `string`                    | Empty/missing strings cause the snippet to be dropped during parse.        |
| `bbox`            | `array<string, float>\|null` | `{x, y, width, height}`. Stored as JSON in `procedure_result`.            |
| `bboxSource`      | `?string`                   | One of `BBOX_SOURCES` — anything else is coerced to `null` defensively.   |
| `bboxConfidence`  | `?float`                    |                                                                            |
| `bboxTarget`      | `?string`                   | One of `BBOX_TARGETS`.                                                     |

`LabSourceSnippet` is reused for intake answers — so this is the
single PHP shape for "where in the source did this fact come from",
regardless of whether the fact is a lab row or a form field.

### `IntakeIngestionRequest`

Aggregate input to `AiIntakeIngestionService::ingest()`.

| Field                  | Type                  | Notes                                                       |
|------------------------|-----------------------|-------------------------------------------------------------|
| `jobId`                | `int`                 |                                                              |
| `documentId`           | `int`                 |                                                              |
| `documentUuid`         | `string`              |                                                              |
| `patientId`            | `int`                 |                                                              |
| `userId`               | `int`                 |                                                              |
| `filename`             | `string`              | Falls back to `"intake.pdf"` if missing.                    |
| `modelId`              | `string`              |                                                              |
| `extractionConfidence` | `?float`              |                                                              |
| `answers`              | `list<IntakeAnswer>`  | Filtered to `fact_type === 'intake_answer'`.                |

### `IntakeAnswer`

```php
public const ANSWER_TYPES = ['string', 'boolean', 'choice', 'integer', 'decimal', 'date'];
```

| Field             | Type                        | Notes                                                                                    |
|-------------------|-----------------------------|------------------------------------------------------------------------------------------|
| `linkId`          | `string`                    | Falls back to `"q{position}"` (1-based) when the LLM doesn't emit one.                  |
| `question`        | `string`                    | Falls back to `label`. If neither is present, `fromExtraction()` returns `null`.        |
| `answerType`      | `string`                    | Coerced to `"string"` if not in `ANSWER_TYPES`.                                         |
| `answerText`      | `?string`                   | Falls back to `value_text`.                                                              |
| `answerOptions`   | `list<string>`              | Empty/whitespace strings dropped.                                                         |
| `sourceSnippets`  | `list<LabSourceSnippet>`    | Reuses the lab snippet shape.                                                            |

### `ResponseMeta` (PHP)

The audit-log envelope on the PHP side — consumed by every controller that
talks to the sidecar (brief, chat, document extract). Source:
[`DTO/ResponseMeta.php`](interface/modules/custom_modules/oe-module-ai-agent/src/DTO/ResponseMeta.php).

| Field               | Type                                 | Notes                                                  |
|---------------------|--------------------------------------|--------------------------------------------------------|
| `promptTokens`      | `int`                                |                                                        |
| `completionTokens`  | `int`                                |                                                        |
| `totalTokens`       | `int`                                |                                                        |
| `costUsd`           | `float`                              |                                                        |
| `latencyMsTotal`    | `int`                                |                                                        |
| `steps`             | `list<array<string, mixed>>`         | Flat step records `{name, duration_ms, status, ...}`.  |

`costUsdMicros()` returns the cost as an integer for `LlmCallLogEntry`'s
DB column (1 USD = 1_000_000 micros), avoiding float drift in audit
storage.

### `LlmCallLogEntry` and `LlmCallVerificationStatus`

The audit log row written by `AuditLogService::record()` after every
extraction. Carries hashes (not bodies), token counts, latency, cost,
verification status, and step-trace JSON. Action type is one of the
`AuditLogService::ACTION_*` constants — for the ingestion path it's always
`ACTION_DOCUMENT_EXTRACT`.

`LlmCallVerificationStatus` is a unit enum (`Passed`, `Failed`) — the
verifier hasn't been wired into the document path yet (only chat/brief),
so for ingestion `Passed` means "sidecar returned a valid envelope" rather
than "verifier accepted every fact".

## Defensive Parsing Conventions

Both layers treat the wire format as untrusted:

- **Pydantic** rejects unknown enum values with a 422 before user code
  runs. Frozen models prevent accidental mutation in async handlers.
- **PHP DTOs** never throw on shape mismatches inside `fromExtraction()`
  — non-array entries are skipped, missing required strings cause that
  one row to be dropped, and unknown enum values coerce to safe defaults
  (`answer_type → "string"`, unknown `bbox_source → null`). This keeps
  partial extractions ingesting whatever they can.

This asymmetry is intentional: the sidecar is strict on the way in (so
the LLM has a tight schema to satisfy) and the PHP side is forgiving on
the way out (so a flaky model response still produces some value rather
than failing the whole job).
