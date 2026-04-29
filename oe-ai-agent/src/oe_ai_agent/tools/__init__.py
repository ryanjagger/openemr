"""FHIR-backed tools the agent invokes during fetch_context."""

from oe_ai_agent.tools.active_medications import get_active_medications
from oe_ai_agent.tools.active_problems import get_active_problems
from oe_ai_agent.tools.allergies import get_allergies
from oe_ai_agent.tools.demographics import get_demographics
from oe_ai_agent.tools.fhir_client import FhirClient, FhirError
from oe_ai_agent.tools.lab_trend import get_lab_trend
from oe_ai_agent.tools.recent_encounters import get_recent_encounters
from oe_ai_agent.tools.recent_notes import get_recent_notes
from oe_ai_agent.tools.recent_observations import get_recent_observations

__all__ = [
    "FhirClient",
    "FhirError",
    "get_active_medications",
    "get_active_problems",
    "get_allergies",
    "get_demographics",
    "get_lab_trend",
    "get_recent_encounters",
    "get_recent_notes",
    "get_recent_observations",
]
