"""Catalog, events, alarm panel, retries and the enabled filter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.prostocam.const import RETRY_MAX
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import TOKEN, calls_to, sent_events

DOOR = "binary_sensor.front_door"
MOTION = "binary_sensor.hall_motion"
PANEL = "alarm_control_panel.home"


def _door(hass: HomeAssistant, state: str, **extra: Any) -> None:
    hass.states.async_set(
        DOOR, state, {"device_class": "door", "friendly_name": "Front door", **extra}
    )


def _populate(hass: HomeAssistant) -> None:
    _door(hass, "off")
    hass.states.async_set(MOTION, "off", {"device_class": "motion"})
    hass.states.async_set("binary_sensor.door_battery", "off", {"device_class": "battery"})
    hass.states.async_set("binary_sensor.no_class", "off")
    hass.states.async_set("sensor.power", "100")
    hass.states.async_set("light.kitchen", "off")
    hass.states.async_set(PANEL, "disarmed")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_catalog_only_matching_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The catalog holds security entities only, and carries the token."""
    mock_server()
    _populate(hass)
    await _setup(hass, config_entry)

    catalogs = calls_to(aioclient_mock, "entities")
    assert len(catalogs) == 1
    entities = catalogs[0][2]["entities"]
    assert [item["entity_id"] for item in entities] == [PANEL, DOOR, MOTION]
    door = next(item for item in entities if item["entity_id"] == DOOR)
    assert door["device_class"] == "door"
    assert door["name"] == "Front door"
    assert door["domain"] == "binary_sensor"
    assert catalogs[0][3]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(catalogs[0][2])
    assert len(calls_to(aioclient_mock, "heartbeat")) == 1


async def test_events_only_for_enabled_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Only transitions of entities enabled on the server are sent."""
    mock_server(enabled=[DOOR])
    _populate(hass)
    await _setup(hass, config_entry)

    _door(hass, "on")
    hass.states.async_set(MOTION, "on", {"device_class": "motion"})
    await hass.async_block_till_done()
    # An attribute change is not a transition.
    _door(hass, "on", battery=50)
    # Restarts and lost links are not transitions either.
    _door(hass, "unavailable")
    _door(hass, "on")
    await hass.async_block_till_done()

    events = sent_events(aioclient_mock)
    assert [(e["entity_id"], e["from_state"], e["state"]) for e in events] == [
        (DOOR, "off", "on")
    ]
    assert events[0]["seq"] == 1
    assert events[0]["device_class"] == "door"
    assert events[0]["changed_at"]
    call = calls_to(aioclient_mock, "events")[0][2]
    assert call["stream"]
    assert call["sent_at"]


async def test_enabled_list_follows_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The heartbeat reply changes which entities are sent."""
    mock_server(enabled=[])
    _populate(hass)
    await _setup(hass, config_entry)

    _door(hass, "on")
    await hass.async_block_till_done()
    assert sent_events(aioclient_mock) == []

    aioclient_mock.clear_requests()
    mock_server(enabled=[DOOR])
    await config_entry.runtime_data.async_heartbeat()
    _door(hass, "off")
    await hass.async_block_till_done()
    assert [(e["entity_id"], e["state"]) for e in sent_events(aioclient_mock)] == [
        (DOOR, "off")
    ]


async def test_alarm_panel_goes_to_alarm(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Arming and disarming go to /alarm, sensors to /events, order kept."""
    mock_server()
    _populate(hass)
    await _setup(hass, config_entry)

    hass.states.async_set(PANEL, "armed_away")
    await hass.async_block_till_done()
    _door(hass, "on")
    await hass.async_block_till_done()

    alarm = sent_events(aioclient_mock, "alarm")
    assert [(e["entity_id"], e["from_state"], e["state"]) for e in alarm] == [
        (PANEL, "disarmed", "armed_away")
    ]
    events = sent_events(aioclient_mock)
    assert [e["entity_id"] for e in events] == [DOOR]
    assert events[0]["seq"] == alarm[0]["seq"] + 1


@pytest.mark.parametrize(
    "failure",
    [{"status": 503}, {"status": 429}, {"exc": aiohttp.ClientConnectionError()}],
)
async def test_events_survive_outage(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    failure: dict[str, Any],
) -> None:
    """Events stay queued while the server is down and go out in order later."""
    mock_server(events=failure)
    _populate(hass)
    await _setup(hass, config_entry)
    bridge = config_entry.runtime_data

    _door(hass, "on")
    await hass.async_block_till_done()
    _door(hass, "off")
    await hass.async_block_till_done()
    assert len(calls_to(aioclient_mock, "events")) == 2
    assert [item["seq"] for item in bridge.queue] == [1, 2]

    aioclient_mock.clear_requests()
    mock_server()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RETRY_MAX + 1))
    await hass.async_block_till_done()

    events = sent_events(aioclient_mock)
    assert [(e["seq"], e["state"]) for e in events] == [(1, "on"), (2, "off")]
    assert len(calls_to(aioclient_mock, "events")) == 1
    assert not bridge.queue


async def test_refused_batch_is_dropped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A batch the server refuses (422) is not retried forever."""
    mock_server(events={"status": 422, "json": {"error": {"code": "invalid"}}})
    _populate(hass)
    await _setup(hass, config_entry)

    _door(hass, "on")
    await hass.async_block_till_done()
    bridge = config_entry.runtime_data
    assert not bridge.queue
    assert bridge.stats["rejected"] == 1


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A revoked token keeps the queue and asks for a new code."""
    mock_server(events={"status": 401})
    _populate(hass)
    await _setup(hass, config_entry)

    _door(hass, "on")
    await hass.async_block_till_done()

    bridge = config_entry.runtime_data
    assert bridge.auth_failed
    assert len(bridge.queue) == 1
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_setup_with_revoked_token(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A token rejected at start fails the setup with a reauth request."""
    mock_server(heartbeat={"status": 401})
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_setup_while_server_down(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """An unreachable server does not block the start."""
    mock_server(heartbeat={"exc": aiohttp.ClientConnectionError()})
    _populate(hass)
    await _setup(hass, config_entry)
    # Without the enabled list nothing is sent yet, but the catalog went out.
    assert len(calls_to(aioclient_mock, "entities")) == 1


async def test_queue_survives_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    hass_storage: dict[str, Any],
) -> None:
    """Unsent events are stored on unload and sent after the next start."""
    mock_server(events={"status": 503})
    _populate(hass)
    await _setup(hass, config_entry)
    _door(hass, "on")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    stored = hass_storage[f"prostocam.{config_entry.entry_id}"]["data"]
    assert [item["entity_id"] for item in stored["pending"]] == [DOOR]
    assert stored["seq"] == 1

    aioclient_mock.clear_requests()
    mock_server()
    await _setup(hass, config_entry)
    events = sent_events(aioclient_mock)
    assert [(e["seq"], e["entity_id"], e["state"]) for e in events] == [(1, DOOR, "on")]
    assert calls_to(aioclient_mock, "events")[0][2]["stream"] == stored["stream"]
    assert not config_entry.runtime_data.queue
