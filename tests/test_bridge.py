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

from custom_components.prostocam.const import DOMAIN, ISSUE_OUTDATED, RETRY_MAX
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .conftest import TOKEN, calls_to, sent_alarms, sent_events

DOOR = "binary_sensor.front_door"
MOTION = "binary_sensor.hall_motion"
PANEL = "alarm_control_panel.home"
SIREN = "siren.hall"


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
    hass.states.async_set("lock.front", "locked")
    hass.states.async_set(SIREN, "off")
    hass.states.async_set(PANEL, "disarmed", {"changed_by": "Owner"})


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
    assert set(catalogs[0][2]) == {"entities"}
    entities = catalogs[0][2]["entities"]
    assert [item["entity_id"] for item in entities] == [PANEL, DOOR, MOTION, SIREN]
    door = next(item for item in entities if item["entity_id"] == DOOR)
    assert door == {
        "entity_id": DOOR,
        "name": "Front door",
        "device_class": "door",
        "area": None,
        "manufacturer": None,
        "model": None,
        "platform": None,
    }
    assert catalogs[0][3]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(catalogs[0][2])

    heartbeats = calls_to(aioclient_mock, "heartbeat")
    assert len(heartbeats) == 1
    assert set(heartbeats[0][2]) == {"client_version", "ha_version"}


async def test_initial_alarm_mode_at_start(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The mode of the panel at start goes out as `initial`, not an event."""
    mock_server()
    _populate(hass)
    await _setup(hass, config_entry)

    alarms = sent_alarms(aioclient_mock)
    assert len(alarms) == 1
    body = alarms[0]
    assert body["entity_id"] == PANEL
    assert body["state"] == "disarmed"
    assert body["initial"] is True
    assert body["user"] == "Owner"
    assert body["seq"] == 1
    assert body["stream"]
    assert "from_state" not in body
    assert set(body) <= {
        "stream",
        "seq",
        "entity_id",
        "state",
        "from_state",
        "friendly_name",
        "user",
        "changed_at",
        "initial",
    }


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
    hass.states.async_set(SIREN, "on")
    await hass.async_block_till_done()
    # An attribute change is not a transition.
    _door(hass, "on", battery=50)
    # Restarts and lost links are not transitions either.
    _door(hass, "unavailable")
    _door(hass, "on")
    await hass.async_block_till_done()

    events = sent_events(aioclient_mock)
    assert [(e["entity_id"], e["from_state"], e["to_state"]) for e in events] == [
        (DOOR, "off", "on")
    ]
    event = events[0]
    assert event["seq"] == 2  # 1 is the initial mode of the alarm panel
    assert event["device_class"] == "door"
    assert event["friendly_name"] == "Front door"
    assert event["changed_at"]
    body = calls_to(aioclient_mock, "events")[0][2]
    assert set(body) == {"stream", "events"}
    assert body["stream"] == sent_alarms(aioclient_mock)[0]["stream"]


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
    assert [(e["entity_id"], e["to_state"]) for e in sent_events(aioclient_mock)] == [
        (DOOR, "off")
    ]


async def test_alarm_panel_goes_to_alarm(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Arming goes to /alarm (even if not enabled), sensors to /events, in order."""
    mock_server(enabled=[DOOR])
    _populate(hass)
    await _setup(hass, config_entry)

    hass.states.async_set(PANEL, "armed_away", {"changed_by": "A very long user name"})
    await hass.async_block_till_done()
    _door(hass, "on")
    await hass.async_block_till_done()

    alarms = sent_alarms(aioclient_mock)
    assert [(a["from_state"], a["state"]) for a in alarms[1:]] == [
        ("disarmed", "armed_away")
    ]
    assert "initial" not in alarms[1]
    assert alarms[1]["user"] == "A very long user"
    events = sent_events(aioclient_mock)
    assert [e["entity_id"] for e in events] == [DOOR]
    assert events[0]["seq"] == alarms[1]["seq"] + 1


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
    assert [item["seq"] for item in bridge.queue] == [2, 3]

    aioclient_mock.clear_requests()
    mock_server()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RETRY_MAX + 1))
    await hass.async_block_till_done()

    events = sent_events(aioclient_mock)
    assert [(e["seq"], e["to_state"]) for e in events] == [(2, "on"), (3, "off")]
    assert len(calls_to(aioclient_mock, "events")) == 1
    assert not bridge.queue


async def test_refused_batch_is_split_then_dropped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A refused batch (422) is retried one by one; refused singles are dropped."""
    mock_server(events={"status": 503})
    _populate(hass)
    await _setup(hass, config_entry)
    _door(hass, "on")
    await hass.async_block_till_done()
    _door(hass, "off")
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    mock_server(
        events={
            "status": 422,
            "json": {"message": "bad", "code": "invalid_body", "field": "events[0].to_state"},
        }
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RETRY_MAX + 1))
    await hass.async_block_till_done()

    sizes = [len(call[2]["events"]) for call in calls_to(aioclient_mock, "events")]
    assert sizes == [2, 1, 1]
    bridge = config_entry.runtime_data
    assert not bridge.queue
    assert bridge.stats["rejected"] == 2


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A revoked token keeps the queue and asks for a new code."""
    mock_server(events={"status": 401, "json": {"message": "x", "code": "token_invalid"}})
    _populate(hass)
    await _setup(hass, config_entry)

    _door(hass, "on")
    await hass.async_block_till_done()

    bridge = config_entry.runtime_data
    assert bridge.auth_failed
    assert len(bridge.queue) == 1
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_outdated_client_raises_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """HTTP 426 keeps the queue, stops sending and shows a repair issue."""
    mock_server(events={"status": 426, "json": {"message": "x", "code": "client_outdated"}})
    _populate(hass)
    await _setup(hass, config_entry)

    _door(hass, "on")
    await hass.async_block_till_done()
    bridge = config_entry.runtime_data
    assert bridge.outdated
    assert len(bridge.queue) == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_OUTDATED) is not None

    # The server accepts the version again: the issue goes, the queue is sent.
    aioclient_mock.clear_requests()
    mock_server()
    await bridge.async_heartbeat()
    await hass.async_block_till_done()
    assert not bridge.outdated
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_OUTDATED) is None
    assert [e["entity_id"] for e in sent_events(aioclient_mock)] == [DOOR]


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
    assert stored["seq"] == 2

    aioclient_mock.clear_requests()
    mock_server()
    await _setup(hass, config_entry)
    events = sent_events(aioclient_mock)
    assert [(e["seq"], e["entity_id"], e["to_state"]) for e in events] == [
        (2, DOOR, "on")
    ]
    assert calls_to(aioclient_mock, "events")[0][2]["stream"] == stored["stream"]
    # The new start reports the panel mode again, with the next number.
    assert sent_alarms(aioclient_mock)[-1]["seq"] == 3
    assert not config_entry.runtime_data.queue
