"""HTTP client for the ProstoCAM server."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import Any

import aiohttp

from .const import (
    API_PREFIX,
    PATH_PAIR,
    PATH_STREAM,
    REQUEST_TIMEOUT,
    SCOPE_ERRORS,
    SSE_READ_TIMEOUT,
    VERSION,
)
from .words import language_of


class ProstoCamError(Exception):
    """Base error of the ProstoCAM client."""

    # The error code of the server reply (`webrtc_unavailable` …), when there is one.
    code: str | None = None


class ProstoCamConnectionError(ProstoCamError):
    """The server can not be reached or is busy; the request may be retried."""


class ProstoCamRateLimitedError(ProstoCamConnectionError):
    """Too many requests (HTTP 429); retry later."""


class ProstoCamUnavailableError(ProstoCamConnectionError):
    """HTTP 503 with a reason (no video right now); `retry_after` in seconds."""

    def __init__(self, code: str | None, retry_after: int | None) -> None:
        """Remember the error code and the pause the server asked for."""
        super().__init__(" ".join(part for part in ("HTTP 503", code) if part))
        self.code = code
        self.retry_after = retry_after


class ProstoCamAuthError(ProstoCamError):
    """The token was rejected (HTTP 401/403)."""


class ProstoCamScopeError(ProstoCamError):
    """The token is valid but lacks an access area (HTTP 403 scope_missing)."""

    def __init__(self, code: str | None, message: str | None) -> None:
        """Remember the error code; the server message names the missing area."""
        super().__init__(f"HTTP 403 {code}")
        self.code = code
        self.message = message


class ProstoCamOutdatedError(ProstoCamError):
    """The server needs a newer version of the integration (HTTP 426)."""


class ProstoCamRejectedError(ProstoCamError):
    """The server refused the request itself; retrying will not help."""

    def __init__(
        self,
        status: int,
        code: str | None,
        field: str | None = None,
        *,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Remember the HTTP status, the error code, the refused field and the details."""
        super().__init__(
            " ".join(part for part in (f"HTTP {status}", code, field) if part)
        )
        self.status = status
        self.code = code
        self.field = field
        # The phrase for a human, in the language of the server (never a secret).
        self.message = message
        # The rest of the refusal: `uncovered`, `revision`, `reason` …
        self.detail = detail or {}


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


def _error_message(body: dict[str, Any]) -> str | None:
    error = body.get("error")
    if isinstance(error, dict):
        body = error
    message = body.get("message")
    return message if isinstance(message, str) else None


def _retry_after(headers: Any) -> int | None:
    try:
        value = int(str(headers.get("Retry-After", "")).strip())
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def raise_for_reply(status: int, text: str, headers: Any) -> dict[str, Any]:
    """Turn an error reply into an exception; return the parsed body otherwise."""
    body = _parse_body(text)
    if status < 400:
        return body
    code, field = _error_code(body)
    if status == 403 and code in SCOPE_ERRORS:
        raise ProstoCamScopeError(code, _error_message(body))
    if status == 401 or (status == 403 and code != "account_inactive"):
        raise ProstoCamAuthError(f"HTTP {status}")
    if status == 426:
        raise ProstoCamOutdatedError(f"HTTP {status}")
    if status == 429:
        limited = ProstoCamRateLimitedError(
            " ".join(part for part in ("HTTP 429", code) if part)
        )
        limited.code = code
        raise limited
    if status == 503 and code:
        raise ProstoCamUnavailableError(code, _retry_after(headers))
    if status >= 500:
        error = ProstoCamConnectionError(
            " ".join(part for part in (f"HTTP {status}", code) if part)
        )
        error.code = code
        raise error
    error = body.get("error")
    detail = error if isinstance(error, dict) else body
    raise ProstoCamRejectedError(
        status,
        code,
        field,
        message=_error_message(body),
        detail={k: v for k, v in detail.items() if k not in ("code", "message")},
    )


class ProstoCamClient:
    """Talks to `/v2/smart-home/ha/*` and `/v2/stream` of a ProstoCAM server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        server: str,
        token: str | None = None,
        language: Callable[[], str | None] | None = None,
    ) -> None:
        """Create a client; without a token only pairing is possible.

        `language` gives the language of Home Assistant at the moment of each
        request: the server answers its phrases (refusals, "no clip") in it.
        """
        self._session = session
        self._server = server.rstrip("/")
        self._token = token
        self._language = language

    @property
    def server(self) -> str:
        """Base address of the server."""
        return self._server

    async def async_pair(self, code: str, ha_version: str) -> dict[str, Any]:
        """Exchange a pairing code from the ProstoCAM web account for a token."""
        return await self._async_request(
            "post",
            PATH_PAIR,
            {"code": code, "client_version": VERSION, "ha_version": ha_version},
            auth=False,
        )

    async def async_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send an authenticated request and return the `data` of the reply."""
        return await self._async_request("post", path, payload, auth=True)

    async def async_get(self, path: str) -> dict[str, Any]:
        """Read an authenticated JSON resource under the integration prefix."""
        return await self._async_request("get", path, None, auth=True)

    async def async_get_image(self, path: str) -> tuple[bytes, str]:
        """Read an image under the integration prefix: (bytes, content type)."""
        headers = self._headers("image/jpeg")
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.get(
                    f"{self._server}{API_PREFIX}{path}", headers=headers
                )
                content = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ProstoCamConnectionError(type(err).__name__) from err
        if response.status >= 400:
            raise_for_reply(
                response.status, content.decode("utf-8", "replace"), response.headers
            )
        content_type = str(response.headers.get("Content-Type") or "image/jpeg")
        return content, content_type.split(";")[0].strip() or "image/jpeg"

    async def async_open_stream(self, last_event_id: str | None) -> Any:
        """Open the live event channel (SSE); the caller reads and releases it."""
        headers = self._headers("text/event-stream")
        headers["Cache-Control"] = "no-cache"
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        try:
            response = await self._session.get(
                f"{self._server}{PATH_STREAM}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=REQUEST_TIMEOUT,
                    sock_read=SSE_READ_TIMEOUT,
                ),
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ProstoCamConnectionError(type(err).__name__) from err
        if response.status != 200:
            try:
                text = await response.text()
            except (aiohttp.ClientError, TimeoutError, UnicodeDecodeError):
                text = ""
            finally:
                response.release()
            raise_for_reply(response.status, text, response.headers)
            raise ProstoCamConnectionError(f"HTTP {response.status}")
        return response

    def _headers(self, accept: str) -> dict[str, str]:
        if not self._token:
            raise ProstoCamAuthError("no token")
        return {
            **self._plain_headers(accept),
            "Authorization": f"Bearer {self._token}",
        }

    def _plain_headers(self, accept: str) -> dict[str, str]:
        """Headers of every request, with or without the token."""
        headers = {
            "Accept": accept,
            "User-Agent": f"HomeAssistant-ProstoCAM/{VERSION}",
        }
        if self._language is not None:
            headers["Accept-Language"] = language_of(self._language())
        return headers

    async def async_call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Any authenticated JSON request under the integration prefix (protocol 3)."""
        return await self._async_request(
            method, path, payload, auth=True, params=params, extra_headers=headers
        )

    async def _async_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        auth: bool,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._server}{API_PREFIX}{path}"
        if auth:
            headers = self._headers("application/json")
        else:
            headers = self._plain_headers("application/json")
        if extra_headers:
            headers.update(extra_headers)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                if method == "get":
                    response = await self._session.get(url, headers=headers, params=params)
                elif method == "post":
                    response = await self._session.post(
                        url, json=payload, headers=headers, params=params
                    )
                else:
                    response = await self._session.request(
                        method.upper(), url, json=payload, headers=headers, params=params
                    )
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            # The message never contains the token: it is only in the headers.
            raise ProstoCamConnectionError(type(err).__name__) from err

        body = raise_for_reply(response.status, text, response.headers)
        data = body.get("data")
        return data if isinstance(data, dict) else body
