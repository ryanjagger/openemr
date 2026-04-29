# Patient-brief evals

Live-LLM eval harness for the agent. Not a CI test — calls the real model,
costs API credits, and is non-deterministic. Run before merging prompt or
model changes.

The fixtures form a **golden set** in the sense of *Production Evals
Cookbook, Stage 1*: hand-curated input/output pairs that define what
"correct" looks like for the agent. Failures here mean either a real
regression or a fixture that needs updating — not both at once. Twelve
fixtures today, run end-to-end in ~60 seconds against Sonnet.

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# default: claude-sonnet-4-6, all fixtures, freetext types disabled (T3.10)
uv run python evals/run_eval.py --label baseline

# A/B: tweak prompt, re-run with a new label
uv run python evals/run_eval.py --label v2_prompt

# debug the harness without spending credits (uses MockLlmClient.synthesizing)
uv run python evals/run_eval.py --label harness-check --provider mock

# run a subset
uv run python evals/run_eval.py --label only-stale --only 0003

# run only the empty-chart and code-status golden cases
uv run python evals/run_eval.py --label spot-check --only 0006 --only 0008

# enable the freetext types (recent_event / agenda_item)
uv run python evals/run_eval.py --label freetext-on --enable-freetext-types
```

Outputs land in `evals/runs/{ts}_{label}.jsonl` (gitignored). One JSON
object per fixture with: model, items emitted vs verified, drop counts
keyed by verifier rule, type distribution, expectations check, and the
full item / failure payloads.

## Inspect a run

```bash
# items per fixture
jq -r '[.fixture_id, .items_verified] | @tsv' evals/runs/*baseline.jsonl

# rule-fire totals across the run
jq -s '[.[].drop_count_by_rule] | add' evals/runs/*baseline.jsonl

# fixtures whose expectations didn't hold (excluding known limitations)
jq 'select(.known_limitation == false and (.expectations_met | to_entries | any(.value == false)))
    | {fixture_id, expectations_met, items: [.items[].type]}' \
    evals/runs/*baseline.jsonl

# which expectation key failed, per fixture
jq -r '.fixture_id as $id
    | .expectations_met | to_entries[]
    | select(.value == false)
    | [$id, .key] | @tsv' evals/runs/*baseline.jsonl
```

## Authoring fixtures

Files in `evals/fixtures/*.json` — each one a FHIR snapshot keyed by
resource type. The runner mounts respx routes per resource type; query
params are not enforced, so a single Bundle covers any search the tools
issue against that resource.

Date tokens of the form `{{TODAY-30D}}` (or `+30D`) are substituted at
load time so fixtures stay evergreen. Use the offset that exercises the
rule you're targeting:

| Goal | Offset |
|---|---|
| Recent activity passes Tier 1 staleness | `-7D` to `-180D` |
| Triggers `tier1_staleness` for med_current/overdue (365d ceiling) | `-400D` or older |
| Patient demographics last updated recently | `-7D` |

Each fixture supports a flat `expectations` object. All keys are
optional; only those present are checked.

| Key | Meaning |
|---|---|
| `min_verified_items` | At least this many items must survive verification |
| `max_verified_items` | At most this many items may survive |
| `expected_types_present` | Each listed `BriefItemType` must appear at least once |
| `expected_types_absent` | None of these types may appear |
| `expected_citations` | Each `resource_id` must appear in some verified item's citations |
| `forbidden_citations` | None of these IDs may appear in any verified item |
| `must_contain` | Each substring must appear in at least one verified item's `text` (case-insensitive) |
| `must_not_contain` | None of these substrings may appear in any verified item's `text` |
| `expected_drop_rules` | `{rule_name: min_count}` — verifier must drop ≥ N items by that rule |

A fixture may also set `"known_limitation": true` at the top level. The
runner still records expectations status, but failures from these
fixtures are tallied separately in the summary line — they document
gaps the verifier is *expected* to leave open today (e.g. semantic
negation, drug-allergy conflict). Today: `0002_negation_in_notes` and
`0012_pcn_amoxicillin_conflict`.

Expectations are loose by design. The LLM is non-deterministic; assert on
shape, citation IDs, and key clinical terms — not exact paraphrase.

## When to add a golden case

- A real chart in production produced the wrong brief. Reduce it to a
  minimal fixture, add the expectation that catches the failure, and
  watch it drive the fix.
- A new `BriefItemType` ships. Add a fixture that exercises it
  cleanly.
- A verifier rule changes (threshold, scope). Add or update a fixture
  that pins the expected behavior.

## When *not* to add a golden case

- The model paraphrased differently than you expected, but the facts
  were correct. That's prompt-tuning territory; don't lock paraphrase
  variance into fixtures.
- You want to test the verifier rules in isolation. Those belong in
  `tests/` as deterministic unit tests; fixtures here exercise the
  whole graph end-to-end.

## What this is **not**

- Not a CI test — see `tests/` for those.
- Not a substitute for human review of fixture additions; the eval shows
  *what changed*, not *whether the change is good*.
- Not a paraphrase-fidelity check (Tier 3 — deferred per ARCH §6.3).
