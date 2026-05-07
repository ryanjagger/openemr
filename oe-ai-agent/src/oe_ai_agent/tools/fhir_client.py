"""Async httpx-based FHIR client.

Stateless: each request is constructed with the user's bearer token (passed
through from PHP). No token caching, no scope expansion. The server-side
scope validator on the FHIR endpoint is the authority on what reads succeed.
"""

from __future__ import annotations

from typing import Any

import httpx


class FhirError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FhirClient:
    DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        http: httpx.AsyncClient | None = None,
        request_id: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._http = http
        self._owned_http = http is None
        self._request_id = request_id

    async def __aenter__(self) -> FhirClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owned_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("FhirClient must be used as an async context manager")
        return self._http

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Accept": "application/fhir+json",
        }
        if self._request_id is not None:
            # Lets api_log rows for this brief join back to llm_call_log via the
            # shared request_id (ARCH §8.4).
            headers["X-Request-Id"] = self._request_id
        return headers

    def _api_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Accept": "application/json",
        }
        if self._request_id is not None:
            headers["X-Request-Id"] = self._request_id
        return headers

    def _api_base_url(self) -> str:
        if self._base_url.endswith("/fhir"):
            return self._base_url[: -len("/fhir")] + "/api"
        return self._base_url.rstrip("/") + "/../api"

    async def search(
        self,
        resource_type: str,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/{resource_type}"
        response = await self._client.get(url, headers=self._headers(), params=params or {})
        return self._parse(response)

    async def read(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        url = f"{self._base_url}/{resource_type}/{resource_id}"
        response = await self._client.get(url, headers=self._headers())
        return self._parse(response)

    async def api_get(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._api_base_url()}/{path.lstrip('/')}"
        response = await self._client.get(url, headers=self._api_headers(), params=params or {})
        return self._parse_json(response, "OpenEMR API")

    async def api_post(
        self,
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self._api_base_url()}/{path.lstrip('/')}"
        headers = {**self._api_headers(), "Content-Type": "application/json"}
        response = await self._client.post(url, headers=headers, json=body)
        return self._parse_json(response, "OpenEMR API")

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        return FhirClient._parse_json(response, "FHIR")

    @staticmethod
    def _parse_json(response: httpx.Response, label: str) -> dict[str, Any]:
        if response.status_code != httpx.codes.OK:
            raise FhirError(
                f"{label} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise FhirError(f"{label} response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise FhirError(f"{label} response was not a JSON object")
        return data
