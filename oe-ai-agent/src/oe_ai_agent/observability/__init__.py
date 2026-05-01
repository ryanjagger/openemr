"""Observability module — structured logging, per-step trace, cost capture.

Wired in from the start (per ARCH observability plan): every request gets
a JSON log line plus a per-step trace surfaced both in the response
envelope (so the PHP audit log persists it) and as DEBUG/WARN log lines
(so Railway log search can drill down).
"""

from oe_ai_agent.observability.cost import compute_completion_cost
from oe_ai_agent.observability.langfuse import (
    flush as flush_langfuse,
)
from oe_ai_agent.observability.langfuse import (
    observation as langfuse_observation,
)
from oe_ai_agent.observability.langfuse import (
    request_trace as langfuse_request_trace,
)
from oe_ai_agent.observability.langfuse import (
    shutdown as shutdown_langfuse,
)
from oe_ai_agent.observability.langfuse import (
    update_current_observation as update_langfuse_observation,
)
from oe_ai_agent.observability.logging_config import configure_logging
from oe_ai_agent.observability.trace import (
    StepRecord,
    TraceCollector,
    bind_request_context,
    current_trace,
    get_logger,
    step,
    use_trace,
)

__all__ = [
    "StepRecord",
    "TraceCollector",
    "bind_request_context",
    "compute_completion_cost",
    "configure_logging",
    "current_trace",
    "flush_langfuse",
    "get_logger",
    "langfuse_observation",
    "langfuse_request_trace",
    "shutdown_langfuse",
    "step",
    "update_langfuse_observation",
    "use_trace",
]
