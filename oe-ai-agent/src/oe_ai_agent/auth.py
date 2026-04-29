"""Internal authentication for sidecar endpoints.

The PHP module is the only intended caller. A shared secret in the
``X-Internal-Auth`` header is checked with constant-time comparison.
This is defense-in-depth on the internal Docker network, not the primary
auth boundary — the sidecar still inherits the user's FHIR ACL via the
bearer token passed in the request body.
"""

from __future__ import annotations

import hmac
from functools import cache
from typing import Annotated

from fastapi import Header, HTTPException, status

from oe_ai_agent.config import Settings, load_settings


@cache
def _get_settings() -> Settings:
    return load_settings()


def require_internal_auth(
    x_internal_auth: Annotated[str | None, Header(alias="X-Internal-Auth")] = None,
) -> None:
    settings = _get_settings()
    if x_internal_auth is None or not hmac.compare_digest(
        x_internal_auth, settings.internal_auth_secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Internal-Auth",
        )
