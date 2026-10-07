"""HTTP client for the ProstoCAM server."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from .const import API_PREFIX, PATH_PAIR, REQUEST_TIMEOUT, VERSION


class ProstoCamError(Exception):
    """Base error of the ProstoCAM client."""


class ProstoCamConnectionError(ProstoCamError):
    """The server can not be reached or is busy; the request may be retried."""


class ProstoCamAuthError(ProstoCamError):
    """The token (or the pairing code) was rejected."""


class ProstoCamRejectedError(ProstoCamError):
    """The server refused the request itself; retrying will not help."""

    def __init__(self, status: int, code: str | None) -> None:
        """Remember the HTTP status and the error code of the reply."""
        super().__init__(f"HTTP {status} {code or ''}".strip())
        self.status = status
        self.code = code


def _parse_body(text: str) -> dict[str, Any]:
    """Parse a JSON reply; anything else is an empty object."""
    if not text:
        return {}
    try:
        body = json.loads(text)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_code(body: dict[str, Any]) -> str | None:
    """Pick the error code out of an error reply."""
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        return str(code) if code is not None else None
    if isinstance(error, str):
        return error
    code = body.get("code")
    return str(code) if code is not None else None


class ProstoCamClient:
    """Talks to `/v2/smart-home/ha/*` of a ProstoCAM server."""

    def __init__(
        self, session: aiohttp.ClientSession, server: str, token: str | None = None
    ) -> None:
        """Create a client; without a token only pairing is possible."""
        self._session = session
        self._server = server.rstrip("/")
        self._token = token

    @property
    def server(self) -> str:
        """Base address of the server."""
        return self._server

    async def async_pair(
        self, code: str, instance_id: str, ha_version: str
    ) -> dict[str, Any]:
        """Exchange a pairing code from the ProstoCAM web account for a token."""
        return await self._async_request(
            PATH_PAIR,
            {
                "code": code,
                "instance_id": instance_id,
                "ha_version": ha_version,
                "client_version": VERSION,
            },
            auth=False,
        )

    async def async_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send an authenticated request and return the `data` of the reply."""
        return await self._async_request(path, payload, auth=True)

    async def _async_request(
        self, path: str, payload: dict[str, Any], *, auth: bool
    ) -> dict[str, Any]:
        url = f"{self._server}{API_PREFIX}{path}"
        headers = {
            "Accept": "application/json",
            "User-Agent": f"HomeAssistant-ProstoCAM/{VERSION}",
        }
        if auth:
            if not self._token:
                raise ProstoCamAuthError("no token")
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.post(url, json=payload, headers=headers)
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            # The message never contains the token: it is only in the headers.
            raise ProstoCamConnectionError(type(err).__name__) from err

        status = response.status
        body = _parse_body(text)
        if status in (401, 403):
            raise ProstoCamAuthError(f"HTTP {status}")
        if status == 429 or status >= 500:
            raise ProstoCamConnectionError(f"HTTP {status}")
        if status >= 400:
            raise ProstoCamRejectedError(status, _error_code(body))
        data = body.get("data")
        return data if isinstance(data, dict) else body
