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


class ProstoCamRateLimitedError(ProstoCamConnectionError):
    """Too many requests (HTTP 429); retry later."""


class ProstoCamAuthError(ProstoCamError):
    """The token was rejected (HTTP 401/403)."""


class ProstoCamOutdatedError(ProstoCamError):
    """The server needs a newer version of the integration (HTTP 426)."""


class ProstoCamRejectedError(ProstoCamError):
    """The server refused the request itself; retrying will not help."""

    def __init__(self, status: int, code: str | None, field: str | None = None) -> None:
        """Remember the HTTP status, the error code and the refused field."""
        super().__init__(
            " ".join(part for part in (f"HTTP {status}", code, field) if part)
        )
        self.status = status
        self.code = code
        self.field = field


def _parse_body(text: str) -> dict[str, Any]:
    """Parse a JSON reply; anything else is an empty object."""
    if not text:
        return {}
    try:
        body = json.loads(text)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, (str, int)) else None


def _error_code(body: dict[str, Any]) -> tuple[str | None, str | None]:
    """Pick the error code and the refused field out of an error reply."""
    error = body.get("error")
    if isinstance(error, dict):
        body = error
    elif isinstance(error, str):
        return error, None
    return _text(body.get("code")), _text(body.get("field"))


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

    async def async_pair(self, code: str, ha_version: str) -> dict[str, Any]:
        """Exchange a pairing code from the ProstoCAM web account for a token."""
        return await self._async_request(
            PATH_PAIR,
            {"code": code, "client_version": VERSION, "ha_version": ha_version},
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
        if status == 426:
            raise ProstoCamOutdatedError(f"HTTP {status}")
        if status == 429:
            raise ProstoCamRateLimitedError(f"HTTP {status}")
        if status >= 500:
            raise ProstoCamConnectionError(f"HTTP {status}")
        if status >= 400:
            code, field = _error_code(body)
            raise ProstoCamRejectedError(status, code, field)
        data = body.get("data")
        return data if isinstance(data, dict) else body
