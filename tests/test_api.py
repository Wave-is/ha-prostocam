"""The HTTP client of ProstoCAM: error replies of every form."""

from __future__ import annotations

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.prostocam.api import (
    ProstoCamAuthError,
    ProstoCamClient,
    ProstoCamConnectionError,
    ProstoCamRateLimitedError,
    ProstoCamRejectedError,
    _error_code,
    _parse_body,
    _retry_after,
    raise_for_reply,
)
from custom_components.prostocam.const import DEFAULT_SERVER
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import BASE, TOKEN
from .test_cameras import STREAM_URL


def test_error_bodies_of_every_form() -> None:
    """Broken JSON, a list, `error` as a word or an object, odd Retry-After."""
    assert _parse_body("{broken") == {}
    assert _parse_body("[1]") == {}
    assert _error_code({"error": "token_invalid"}) == ("token_invalid", None)
    assert _error_code({"error": {"code": "invalid_body", "field": "sdp"}}) == ("invalid_body", "sdp")
    assert _retry_after({"Retry-After": "soon"}) is None
    assert _retry_after({"Retry-After": "-5"}) is None
    assert _retry_after(None) is None

    with pytest.raises(ProstoCamRejectedError) as err:
        raise_for_reply(422, '{"error": {"code": "invalid_body", "message": "sdp", "field": "sdp"}}', {})
    assert err.value.field == "sdp"
    assert err.value.message == "sdp"
    with pytest.raises(ProstoCamRateLimitedError) as limited:
        raise_for_reply(429, '{"code": "ha_live_limit"}', {})
    assert limited.value.code == "ha_live_limit"
    with pytest.raises(ProstoCamConnectionError) as failed:
        raise_for_reply(502, '{"code": "webrtc_negotiation_failed"}', {})
    assert failed.value.code == "webrtc_negotiation_failed"
    assert raise_for_reply(200, '{"data": {}}', {}) == {"data": {}}


async def test_client_without_a_token_and_lost_connections(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No token: no request; a lost connection is a connection error; odd stream replies."""
    session = async_get_clientsession(hass)
    with pytest.raises(ProstoCamAuthError):
        await ProstoCamClient(session, DEFAULT_SERVER).async_get("/cameras")

    client = ProstoCamClient(session, DEFAULT_SERVER, TOKEN)
    aioclient_mock.get(f"{BASE}/cameras/12/snapshot", exc=aiohttp.ClientError())
    with pytest.raises(ProstoCamConnectionError):
        await client.async_get_image("/cameras/12/snapshot")

    aioclient_mock.get(STREAM_URL, status=204, text="")
    with pytest.raises(ProstoCamConnectionError, match="HTTP 204"):
        await client.async_open_stream(None)

    aioclient_mock.clear_requests()
    aioclient_mock.get(STREAM_URL, exc=aiohttp.ClientError())
    with pytest.raises(ProstoCamConnectionError):
        await client.async_open_stream("cursor")
