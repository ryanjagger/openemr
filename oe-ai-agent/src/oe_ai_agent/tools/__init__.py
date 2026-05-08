"""FHIR-backed tools the agent invokes during fetch_context."""

from oe_ai_agent.tools.active_medications import get_active_medications
from oe_ai_agent.tools.active_problems import get_active_problems
from oe_ai_agent.tools.allergies import get_allergies
from oe_ai_agent.tools.appointments import get_appointments
from oe_ai_agent.tools.care_plan_goals import get_care_plan_goals
from oe_ai_agent.tools.clinical_guidelines import search_clinical_guidelines
from oe_ai_agent.tools.demographics import get_demographics
from oe_ai_agent.tools.fhir_client import FhirClient, FhirError
from oe_ai_agent.tools.immunizations import get_immunizations
from oe_ai_agent.tools.lab_trend import get_lab_trend
from oe_ai_agent.tools.medication_history import get_medication_history
from oe_ai_agent.tools.observation_search import get_observations
from oe_ai_agent.tools.orders import get_orders
from oe_ai_agent.tools.procedures import get_procedures
from oe_ai_agent.tools.questionnaire_responses import get_questionnaire_responses
from oe_ai_agent.tools.recent_encounters import get_recent_encounters
from oe_ai_agent.tools.recent_notes import get_recent_notes
from oe_ai_agent.tools.recent_observations import get_recent_observations

__all__ = [
    "FhirClient",
    "FhirError",
    "get_active_medications",
    "get_active_problems",
    "get_allergies",
    "get_appointments",
    "get_care_plan_goals",
    "get_demographics",
    "get_immunizations",
    "get_lab_trend",
    "get_medication_history",
    "get_observations",
    "get_orders",
    "get_procedures",
    "get_questionnaire_responses",
    "get_recent_encounters",
    "get_recent_notes",
    "get_recent_observations",
    "search_clinical_guidelines",
]
