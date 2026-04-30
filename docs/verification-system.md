Why the verification system exists

  The agent reads a patient's chart through FHIR APIs and asks Claude to produce a short brief — meds, allergies, recent activity, overdue items. Large
  language models are good at this kind of paraphrase but they will, given any opportunity, invent things: a value off by a digit, a date that drifts a week, a
   medication the patient isn't actually on. In a clinical context that's not a quality issue, it's a safety one. The verification system is what makes the
  brief safe to put in front of a physician without manual review of every line.

  The design choice

  We use a deterministic verifier, not a second LLM. That is, every check is plain Python operating on data, not another model judging the first one's work. A
  second LLM is non-deterministic, expensive, and shifts the trust problem rather than solving it. Plain-Python checks run in milliseconds, never disagree with
   themselves, and can be reasoned about line-by-line. The trade-off is that a deterministic verifier can only catch the structural shape of hallucination
  (made-up IDs, wrong patient, drifted numbers), not the semantic shape ("denies chest pain" rendered as "has chest pain"). We address that gap explicitly in
  the "What this is not" section below.

  The flow

  The agent runs as a graph: fetch chart data → call the model with the data → parse what came back → verify → render. Every item the model produces is a
  structured object with a type (e.g. med_current, allergy), a paraphrased text, and a list of citations pointing at the FHIR rows it was derived from. The
  verifier walks each item through a chain of rules. If any rule fails, the item is dropped and the failure is logged with the rule name. Only items that pass
  every rule reach the rendered brief.

  Tier 1 — structural integrity

  Five rules, all answering the question "does this claim trace back to real chart data?"

  1. Citations exist. Every cited row ID must be one the model actually saw in this run. Catches IDs the model made up wholesale.
  2. Patient binding. Every cited row's patient ID must match the request's patient. Catches cross-patient leaks — the highest-severity failure mode in this
  system.
  3. Type/source compatibility. A med_current item can only cite a MedicationRequest. An allergy item can only cite an AllergyIntolerance. Catches type
  confusion.
  4. Re-extraction. Every number and date in the paraphrase must appear literally in the cited row. This is the rule that catches "creatinine 1.8" turning into
   "creatinine 8.1" or a date drifting by a day.
  5. Staleness. Each item type has a freshness ceiling — current meds 365 days, recent events 180 days, etc. Items whose youngest source is older than the
  ceiling are dropped, because old data presented as current is itself a falsehood.

  Tier 2 — shape and policy

  Three rules covering things the schema can't express:

  1. Citation floor. Every item must cite at least one row. No free-floating claims.
  2. Advisory denylist. The agent paraphrases; it does not advise. Phrases like "I recommend," "you should," "rule out," "probably has" cause the item to drop.
   This is a tripwire, not a fence — the real defense against advisory output is the system prompt — but it catches the most overt slips.
  3. Type gating. Some item types (free-text-derived recent_event, agenda_item) can be disabled by deployment configuration. The verifier drops them as a
  defense-in-depth backstop in case the upstream schema narrowing fails.

  What survives, what doesn't

  In practice, on the eval fixtures: a clean chart produces ~5 verified items out of 5–6 candidates. A chart with stale meds produces 1 verified item with 2
  staleness drops recorded. A chart with negation in the notes produces 1–2 verified items with re-extraction drops recorded. Every drop is auditable: the
  audit log records the rule that fired, the request ID, and a hash-chained checksum so the trail can't be rewritten silently.

  What this is not

  It is not a paraphrase-fidelity check. If the chart says "denies chest pain" and the model writes "chest pain," every rule above will pass — the words are in
   the source. That class of failure requires semantic comparison (Tier 3 in the architecture doc), which is deferred. The current system is calibrated to
  catch fabrication and transposition, not semantic flip. The eval suite includes a fixture that exercises this gap so we know the day Tier 3 lands whether it
  actually closed the hole.

  The summary: the verifier is a cheap, deterministic filter that turns "LLM probably got this right" into "every claim in this brief traces literally back to
  chart data of the right type, patient, age, and content." That guarantee is the product.