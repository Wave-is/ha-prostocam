"""A server in trouble is not a revoked token (1.0.2).

18.11.2026 the database of the server was recreated and for six minutes the token
check answered `401 token_invalid`; 1.0.1 believed the first 401, asked to pair
again and stopped polling. Now:

* `401 integration_revoked` (the connection was removed in the web account) asks
  for a new pairing at once;
* any other 401 only when it repeats — AUTH_FAILURES_TO_REAUTH in a row, the first
  one AUTH_FAILURE_WINDOW ago; until then the bridge retries and the entities are
  unavailable;
* 5xx / `503 temporarily_unavailable` / the network: retries, entities unavailable,
  no repair;
* a request to pair that is already shown is withdrawn by the first accepted
  heartbeat.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.prostocam.api import (
    ProstoCamAuthError,
    ProstoCamUnavailableError,
    raise_for_reply,
)
from custom_components.prostocam.const import (
    AUTH_FAILURE_WINDOW,
    AUTH_FAILURES_TO_REAUTH,
    DATA_AUTH_STRIKES,
    ERROR_INTEGRATION_REVOKED,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .test_v3 import entity
from .test_v4 import setup_v4

PASSING = ProstoCamAuthError("HTTP 401 token_invalid", "token_invalid")
REVOKED = ProstoCamAuthError("HTTP 401 integration_revoked", ERROR_INTEGRATION_REVOKED)


def _reauth_flows(hass: HomeAssistant) -> list[dict[str, Any]]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _beat(hass: HomeAssistant, bridge: Any, error: Exception | None) -> None:
    if error is None:
        await bridge.async_heartbeat()
    else:
        with patch.object(bridge.client, "async_post", side_effect=error):
            await bridge.async_heartbeat()
    await hass.async_block_till_done()


def test_the_code_of_a_401_is_kept() -> None:
    """`integration_revoked` is told apart from a plain 401."""
    with pytest.raises(ProstoCamAuthError) as revoked:
        raise_for_reply(401, '{"code": "integration_revoked", "message": "x"}', {})
    assert revoked.value.revoked
    assert revoked.value.code == ERROR_INTEGRATION_REVOKED
    with pytest.raises(ProstoCamAuthError) as plain:
        raise_for_reply(401, '{"code": "token_invalid", "message": "x"}', {})
    assert not plain.value.revoked
    with pytest.raises(ProstoCamUnavailableError) as busy:
        raise_for_reply(503, '{"code": "temporarily_unavailable"}', {"Retry-After": "30"})
    assert busy.value.retry_after == 30


async def test_a_passing_401_retries_and_does_not_ask_to_pair(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A 401 while the server is in trouble: entities unavailable, no repair, back by itself."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server)
    bridge = hub.bridge
    camera = entity(hass, "camera", "42_12_camera")
    assert camera is not None
    assert hass.states.get(camera).state != STATE_UNAVAILABLE

    for _ in range(AUTH_FAILURES_TO_REAUTH + 2):
        await _beat(hass, bridge, PASSING)
    assert not bridge.auth_failed
    assert bridge.server_unreachable
    assert not _reauth_flows(hass)
    assert hass.states.get(camera).state == STATE_UNAVAILABLE
    # A reader of the cameras that hears the same 401 keeps its data and tries again.
    assert not bridge.report_auth_failed(PASSING)

    await _beat(hass, bridge, None)
    assert not bridge.server_unreachable
    assert hass.states.get(camera).state != STATE_UNAVAILABLE
    assert hass.data[DATA_AUTH_STRIKES][config_entry.entry_id]["count"] == 0
    assert bridge.diagnostics()["auth_refusals_in_a_row"] == 0


async def test_an_outage_makes_entities_unavailable_without_a_repair(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`503 temporarily_unavailable`: unavailable, no request to pair, then back."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server)
    bridge = hub.bridge
    camera = entity(hass, "camera", "42_12_camera")
    assert camera is not None

    await _beat(hass, bridge, ProstoCamUnavailableError("temporarily_unavailable", 30))
    assert bridge.server_unreachable
    assert not bridge.auth_failed
    assert not _reauth_flows(hass)
    assert hass.states.get(camera).state == STATE_UNAVAILABLE

    await _beat(hass, bridge, None)
    assert hass.states.get(camera).state != STATE_UNAVAILABLE


async def test_a_401_that_lasts_ten_minutes_asks_to_pair(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Many refusals in a short while are not enough; the same refusal 10 minutes later is."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server)
    bridge = hub.bridge
    clock = _Clock()
    bridge._clock = clock

    for _ in range(AUTH_FAILURES_TO_REAUTH * 3):
        await _beat(hass, bridge, PASSING)
        clock.now += 10
    assert not bridge.auth_failed
    assert not _reauth_flows(hass)

    clock.now += AUTH_FAILURE_WINDOW
    await _beat(hass, bridge, PASSING)
    assert bridge.auth_failed
    assert len(_reauth_flows(hass)) == 1


async def test_a_revoked_connection_asks_at_once_and_an_accepted_heartbeat_withdraws(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`integration_revoked` asks at once; the next accepted heartbeat takes the request back."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server)
    bridge = hub.bridge

    await _beat(hass, bridge, REVOKED)
    assert bridge.auth_failed
    assert len(_reauth_flows(hass)) == 1

    # Heartbeats go on while the request is shown; the server accepts the token again.
    await _beat(hass, bridge, None)
    assert not bridge.auth_failed
    assert not bridge.server_unreachable
    assert not _reauth_flows(hass)
    # The event channel is open again.
    assert hub._events_task is not None and not hub._events_task.done()
    hub.async_resume()  # already running: nothing more


async def test_a_plain_401_at_start_does_not_fail_the_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Home Assistant started while the server was in trouble: loaded, retrying, no reauth."""
    mock_server(heartbeat={"status": 401, "json": {"message": "x", "code": "token_invalid"}})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert not _reauth_flows(hass)
    bridge = config_entry.runtime_data
    assert bridge.server_unreachable
    assert not bridge.auth_failed
