"""Static constraints applied by the verifier.

Three sets:
1. ``ALLOWED_TABLES_FOR_TYPE`` — claim-type → FHIR resource types the verifier
   accepts as a citation source. Closes the "med claim cited from a Patient row"
   class of mismatches.
2. ``MAX_AGE_DAYS_FOR_TYPE`` — staleness windows. ``None`` means "no staleness
   limit; render with date but never drop" (e.g. code_status).
3. ``ADVISORY_DENYLIST`` — regex of phrases the agent must not produce. The
   summary agent paraphrases; it does not advise.
"""

from __future__ import annotations

import re

from oe_ai_agent.schemas.brief import BriefItemType

ALLOWED_TABLES_FOR_TYPE: dict[BriefItemType, frozenset[str]] = {
    BriefItemType.MED_CURRENT: frozenset({"MedicationRequest"}),
    BriefItemType.MED_CHANGE: frozenset({"MedicationRequest", "DocumentReference"}),
    BriefItemType.OVERDUE: frozenset({"Observation", "Encounter", "DocumentReference"}),
    BriefItemType.RECENT_EVENT: frozenset({"Encounter", "DocumentReference", "Observation"}),
    BriefItemType.AGENDA_ITEM: frozenset({"DocumentReference", "Encounter"}),
    BriefItemType.CODE_STATUS: frozenset({"Observation", "DocumentReference"}),
    BriefItemType.ALLERGY: frozenset({"AllergyIntolerance"}),
}


MAX_AGE_DAYS_FOR_TYPE: dict[BriefItemType, int | None] = {
    BriefItemType.MED_CURRENT: 365,
    BriefItemType.MED_CHANGE: 180,
    BriefItemType.OVERDUE: None,
    BriefItemType.RECENT_EVENT: 180,
    BriefItemType.AGENDA_ITEM: 90,
    BriefItemType.CODE_STATUS: None,
    BriefItemType.ALLERGY: None,
}


ADVISORY_DENYLIST: re.Pattern[str] = re.compile(
    r"\b("
    r"I recommend|"
    r"you should|"
    r"consider stopping|"
    r"consider starting|"
    r"rule out|"
    r"likely has|"
    r"probably|"
    r"might want to"
    r")\b",
    re.IGNORECASE,
)
