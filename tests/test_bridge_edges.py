"""Rare paths of the bridge: restore overflow, refused catalog, heartbeats, the config of the server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.prostocam.api import (
    ProstoCamAuthError,
    ProstoCamOutdatedError,
    ProstoCamRejectedError,
)
from custom_components.prostocam.bridge import _battery_level
from custom_components.prostocam.const import DOMAIN, MAX_QUEUE
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import BASE, calls_to, server_config
from .test_v4 import Replies

DOOR = "binary_sensor.front_door"


async def setup_bridge(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    entities: Replies | None = None,
) -> Any:
    """A protocol 1 entry; `entities` answers the catalog first."""
    if entities is not None:
        aioclient_mock.post(f"{BASE}/entities", side_effect=entities)
    mock_server()
    hass.states.async_set(DOOR, "off", {"device_class": "door"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return config_entry.runtime_data


def test_battery_level_reads_levels_only() -> None:
    """A flag, a word or a value outside 0…100 is no level."""
    assert _battery_level(True) is None
    assert _battery_level("x") is None
    assert _battery_level(140) is None
    assert _battery_level("87.4") == 87


async def test_restored_queue_keeps_the_newest(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    hass_storage: dict[str, Any],
) -> None:
    """More stored events than the queue holds: the oldest are dropped."""
    pending = [
        {"seq": seq, "entity_id": DOOR, "from_state": "off", "to_state": "on", "_wall": "x"}
        for seq in range(1, MAX_QUEUE + 3)
    ]
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {"stream": "s1", "seq": MAX_QUEUE + 2, "pending": pending},
    }
    with patch("custom_components.prostocam.bridge.ProstoCamBridge._schedule_flush"):
        bridge = await setup_bridge(hass, aioclient_mock, config_entry, mock_server)
        assert bridge.stats["dropped"] == 2
        assert len(bridge.queue) == MAX_QUEUE
        assert bridge._queued_s({"_mono": "x"}, 5.0) == 0


async def test_refused_catalog_waits_for_the_next_heartbeat(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A catalog that failed is sent again after the next good heartbeat."""
    entities = Replies((500, {}))
    bridge = await setup_bridge(hass, aioclient_mock, config_entry, mock_server, entities)
    assert bridge._catalog_pending
    entities.replies = [(200, {"data": {"accepted": True, "config": server_config()}})]
    await bridge.async_heartbeat()
    await hass.async_block_till_done()
    assert not bridge._catalog_pending
    sent = len(entities.bodies)
    await bridge.async_send_catalog()  # the same catalog is not sent twice
    assert len(entities.bodies) == sent

    # A new entity of Home Assistant: the catalog is sent again (after the pause).
    hass.states.async_set("binary_sensor.back_door", "off", {"device_class": "door"})
    await hass.async_block_till_done()

    bridge.outdated = True
    await bridge.async_send_catalog()
    assert len(entities.bodies) == sent
    bridge.outdated = False


async def test_heartbeat_paths(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A stopped bridge is silent; a rejected token in a timer starts reauth once."""
    bridge = await setup_bridge(hass, aioclient_mock, config_entry, mock_server)
    beats = len(calls_to(aioclient_mock, "heartbeat"))
    bridge._stopped = True
    await bridge.async_heartbeat()
    bridge._stopped = False
    assert len(calls_to(aioclient_mock, "heartbeat")) == beats

    with patch.object(bridge.client, "async_post", side_effect=ProstoCamAuthError("HTTP 401")):
        await bridge.async_heartbeat()
    assert bridge.auth_failed
    bridge._async_auth_failed()  # once only
    assert len(hass.config_entries.flow.async_progress()) == 1


async def test_server_config_and_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Odd results, a newer protocol, a new interval, refusals of every kind."""
    bridge = await setup_bridge(hass, aioclient_mock, config_entry, mock_server)
    bridge._apply_server_data({"results": ["x", {"status": "accepted"}], "config": "x"})
    assert bridge.stats["outcomes"]["accepted"] == 1

    config = {
        **server_config(),
        "protocol_version": 9,
        "heartbeat_interval_s": 120,
        "battery_levels": True,
        "capabilities": ["smart_home:ingest"],
    }
    with patch.object(hass.config_entries, "async_schedule_reload"):
        bridge._apply_server_data({"config": config})
        bridge._apply_server_data({"config": config})
    assert bridge.server_protocol == 9
    assert bridge.heartbeat_interval == 120
    assert bridge.battery_levels is True

    assert bridge._handle_error(ProstoCamRejectedError(422, "invalid_body", "events[0]")) is False
    assert bridge._handle_error(ProstoCamOutdatedError("HTTP 426")) is False
    bridge._async_outdated()  # once only
    assert bridge.outdated


async def test_filters_and_a_full_queue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Domains the server does not take; a full queue drops its oldest event."""
    bridge = await setup_bridge(hass, aioclient_mock, config_entry, mock_server)
    bridge.catalog_domains = {"siren"}
    assert bridge.build_catalog() == []

    bridge.event_domains = {"alarm_control_panel"}
    hass.states.async_set(DOOR, "on", {"device_class": "door"})
    await hass.async_block_till_done()
    assert not bridge.queue

    bridge.event_domains = {"binary_sensor"}
    with patch.object(bridge, "_schedule_flush"):
        bridge.queue.extend({"seq": n, "_mono": 0.0} for n in range(MAX_QUEUE))
        hass.states.async_set(DOOR, "off", {"device_class": "door"})
        await hass.async_block_till_done()
        assert len(bridge.queue) == MAX_QUEUE
        assert bridge.stats["dropped"] == 1
    bridge.queue.clear()
