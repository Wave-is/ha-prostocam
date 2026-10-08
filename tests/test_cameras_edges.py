"""Refusals and rare paths of the camera hub, the camera entity and the event channel."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.prostocam import (
    async_remove_config_entry_device,
    async_unload_entry,
)
from custom_components.prostocam.cameras import _int, _str
from custom_components.prostocam.const import DOMAIN
from custom_components.prostocam.sse import SseEvent
from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import BASE
from .test_cameras import HLS_URL, JPEG, STREAM_URL, camera_item, device_of, frame
from .test_v4 import Replies, camera_entity, setup_v4, v4_config, v4_items

SERVER_ERROR = (500, {})


def catalog(items: list[Any] | None = None) -> tuple[int, dict[str, Any]]:
    """A reply of `GET /cameras`."""
    return (200, {"data": {"items": v4_items() if items is None else items, "config": v4_config()}})


async def setup_hub(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    routes: dict[tuple[str, str], Replies],
    **kwargs: Any,
) -> Any:
    """Set up a protocol 4 entry; `routes` (path from the server root or the prefix) answer first."""

    def _before(mock: AiohttpClientMocker) -> None:
        for (method, path), replies in routes.items():
            url = path if path.startswith("https://") else f"{BASE}{path}"
            getattr(mock, method)(url, side_effect=replies)

    return await setup_v4(hass, aioclient_mock, config_entry, mock_server, before=_before, **kwargs)


def test_helpers_read_numbers_and_words() -> None:
    """Numbers and words of the server; anything else is nothing."""
    assert _int(True) is None
    assert _int("5") == 5
    assert _int("x") is None
    assert _str("  a ") == "a"
    assert _str(" ") is None


async def test_catalog_failures_and_changes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Failed reads keep the cameras; a renamed camera renames its device; a gone one goes."""
    reads = Replies(catalog([*v4_items(), "bad", {"id": "x"}]))
    hub = await setup_hub(
        hass, aioclient_mock, config_entry, mock_server, {("get", "/cameras"): reads}
    )
    assert set(hub.cameras) == {12, 14}

    reads.replies = [SERVER_ERROR]
    await hub.async_refresh_catalog()
    assert hub.stats["catalog_errors"] == 1
    reads.replies = [(403, {"code": "scope_missing", "message": "cameras:read"})]
    await hub.async_refresh_catalog()
    assert "cameras:read" in hub.denied
    await hub.async_refresh_catalog()  # no area: nothing is asked
    hub.denied.clear()
    reads.replies = [(401, {"code": "token_invalid", "message": "x"})]
    await hub.async_refresh_catalog()
    await hass.async_block_till_done()
    assert hub.bridge.auth_failed
    hub.bridge.auth_failed = False

    renamed = camera_item(12, "Gate 2")
    renamed["features"]["webrtc"] = True
    hub._detect(14, "motion")
    reads.replies = [catalog([renamed])]
    await hub.async_refresh_catalog()
    await hass.async_block_till_done()
    assert device_of(hass, config_entry, "42_camera_12").name == "Gate 2"
    assert device_of(hass, config_entry, "42_camera_14") is None
    assert 14 not in hub.states


async def test_config_changes_that_do_not_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The same access, no areas yet or a stopped hub: no reload."""
    hub = await setup_hub(hass, aioclient_mock, config_entry, mock_server, {})
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        hub._async_config_changed()
        capabilities = hub.bridge.capabilities
        hub.bridge.capabilities = None
        hub._async_config_changed()
        hub.bridge.capabilities = capabilities
        hub._stopped = True
        hub._async_config_changed()
        hub._stopped = False
    assert not reload.called


async def test_camera_for_and_stale_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """An event names a device number; an entity nobody expects goes away."""
    hub = await setup_hub(hass, aioclient_mock, config_entry, mock_server, {})
    hub.cameras[12]["device_id"] = 1012
    assert hub.camera_for(1012) == 12
    assert hub.camera_for(5555) is None
    assert hub.camera_for("x") is None

    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor", DOMAIN, "42_12_gone", config_entry=config_entry
    )
    protocol = hub.protocol
    hub.protocol = None
    hub.async_remove_stale_entities()  # the server was not reached: nothing is removed
    assert registry.async_get(stale.entity_id) is not None
    hub.protocol = protocol
    hub.async_remove_stale_entities()
    assert registry.async_get(stale.entity_id) is None


async def test_live_and_snapshot_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """No address and the old frame on every failure; the pause the server asks for."""
    live = Replies((200, {"data": {"camera_id": 12, "hls_url": HLS_URL}}))
    snapshot = Replies({"status": 200, "response": JPEG, "headers": {"Content-Type": "image/jpeg"}})
    hub = await setup_hub(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        {("get", "/cameras/12/live"): live, ("get", "/cameras/12/snapshot"): snapshot},
    )
    live.replies = [(200, {"data": {"hls_url": "rtsp://x"}}), SERVER_ERROR]
    assert await hub.async_live_url(12) is None
    assert await hub.async_live_url(12) is None
    assert hub.stats["live_errors"] == 2
    live.replies = [(403, {"code": "scope_missing", "message": "live:read"})]
    assert await hub.async_live_url(12) is None
    assert await hub.async_live_url(12) is None  # no area: nothing is asked
    hub.denied.clear()

    assert await hub.async_snapshot(12, fresh=True) == JPEG
    snapshot.replies = [
        {"status": 503, "json": {"code": "snapshot_unavailable"}, "headers": {"Retry-After": "30"}}
    ]
    assert await hub.async_snapshot(12, fresh=True) == JPEG
    assert await hub.async_snapshot(12) == JPEG  # the pause of the server
    snapshot.replies = [{"status": 200, "response": b"", "headers": {"Content-Type": "image/jpeg"}}]
    assert await hub.async_snapshot(12, fresh=True) == JPEG
    snapshot.replies = [(403, {"code": "scope_missing", "message": "cameras:read"})]
    assert await hub.async_snapshot(12, fresh=True) == JPEG
    assert await hub.async_snapshot(12, fresh=True) == JPEG  # no area
    hub.denied.clear()

    live.replies = [(401, {"code": "token_invalid", "message": "x"})]
    assert await hub.async_live_url(12) is None
    await hass.async_block_till_done()
    assert await hub.async_live_url(12) is None  # the token is gone
    snapshot.replies = [(401, {"code": "token_invalid", "message": "x"})]
    hub.bridge.auth_failed = False
    assert await hub.async_snapshot(12, fresh=True) == JPEG


async def test_event_frame_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The frame of an event: missing, empty, refused."""
    frames = Replies((404, {"code": "event_frame_missing", "message": "x"}))
    hub = await setup_hub(
        hass, aioclient_mock, config_entry, mock_server, {("get", "/events/77/snapshot"): frames}
    )
    assert await hub.async_event_image(77) is None
    assert hub.stats["snapshot_errors"] == 0
    frames.replies = [{"status": 200, "response": b"", "headers": {"Content-Type": "image/jpeg"}}]
    assert await hub.async_event_image(77) is None
    frames.replies = [(403, {"code": "scope_missing", "message": "events:read"})]
    assert await hub.async_event_image(77) is None
    assert await hub.async_event_image(77) is None
    hub.denied.clear()
    frames.replies = [(401, {"code": "token_invalid", "message": "x"})]
    assert await hub.async_event_image(77) is None


async def test_event_channel_refusals_and_closes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Pauses after refusals; how a closed channel is opened again."""
    channel = Replies(SERVER_ERROR)
    hub = await setup_hub(
        hass, aioclient_mock, config_entry, mock_server, {("get", STREAM_URL): channel}
    )
    if hub._events_task is not None:
        hub._events_task.cancel()
    await hass.async_block_till_done()

    channel.replies = [(429, {"code": "stream_integration_limit", "message": "x"})]
    assert await hub._async_events_once() >= 60
    channel.replies = [
        {"status": 503, "json": {"code": "stream_not_here"}, "headers": {"Retry-After": "7"}}
    ]
    assert await hub._async_events_once() >= 7

    def closed(reason: str) -> dict[str, Any]:
        text = frame("hello", {"cursor": "c1", "resumed": True}, "c1") + frame(
            "session.closed", {"reason": reason}
        )
        return {"status": 200, "text": text, "headers": {"Content-Type": "text/event-stream"}}

    channel.replies = [closed("token_expired")]
    assert await hub._async_events_once() == 0.5
    channel.replies = [closed("session_revoked")]
    assert await hub._async_events_once() == 3.0
    channel.replies = [{"status": 200, "text": "", "headers": {"Content-Type": "text/event-stream"}}]
    assert await hub._async_events_once() >= 3

    channel.replies = [(403, {"code": "stream_scope_missing", "message": "events:read"})]
    assert await hub._async_events_once() is None
    assert "events:read" in hub.denied
    hub.denied.clear()
    channel.replies = [(401, {"code": "stream_auth_required", "message": "x"})]
    assert await hub._async_events_once() is None
    assert hub.stats["stream_last_error"] == "token_invalid"


async def test_channel_events_of_every_kind(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Broken data, the first cursor, resync, incidents, problems, unknown cameras."""
    hub = await setup_hub(hass, aioclient_mock, config_entry, mock_server, {})
    hub.cursor = None
    assert hub.handle_event(SseEvent("hello", "{broken", None)) is None
    assert hub.handle_event(SseEvent("hello", "[1]", None)) is None
    hub.handle_event(SseEvent("hello", '{"cursor": "c9"}', "c9"))
    assert hub.cursor == "c9"
    hub.handle_event(SseEvent("resync", '{"cursor": "c10"}', None))
    assert hub.cursor == "c10"
    assert hub.handle_event(SseEvent("session.closed", "", None)) == "closed"

    hub.handle_event(SseEvent("incident.opened", '{"incident": {"id": 5, "camera_id": 12, "status": "open"}}', None))
    hub.handle_event(SseEvent("incident.opened", '{"incident": {"camera_id": 99}}', None))
    hub.handle_event(SseEvent("incident.opened", '{"incident": "x"}', None))
    assert hub.states[12].incident["id"] == 5
    hub._handle_alarm({"camera_id": 12, "classification": "person", "event_id": 456})
    assert hub.states[12].last_alarm["incident_id"] == 5
    hub._handle_alarm("x")
    hub._handle_alarm({"camera_id": 99})
    hub.handle_event(SseEvent("incident.closed", '{"incident": {"camera_id": 12}}', None))
    assert hub.states[12].incident is None

    hub.handle_event(SseEvent("problem.opened", '{"problem": {"camera_id": 12, "kind": "no_video"}}', None))
    assert hub.states[12].problems == {"no_video"}
    hub.handle_event(SseEvent("problem.resolved", '{"camera_id": 12, "kind": "no_video"}', None))
    assert hub.states[12].problems == set()
    hub.handle_event(SseEvent("problem.opened", '{"problem": {"camera_id": 12}}', None))
    hub.handle_event(SseEvent("problem.opened", '{"problem": "x"}', None))

    hub.handle_event(SseEvent("camera.status", '{"camera": "x"}', None))
    hub.handle_event(SseEvent("camera.status", '{"camera": {"id": 99}}', None))
    hub.handle_event(SseEvent("camera.status", '{"camera": {"id": 12, "video": {"state": "offline", "streaming": false}}}', None))
    assert hub.states[12].online is False
    assert hub.states[12].streaming is False
    assert hub.stats["unknown_camera_events"] == 2
    await hass.async_block_till_done()


class FakeStream:
    """What the camera entity needs of a `Stream`."""

    def __init__(self, *, running: bool = False, available: bool = False) -> None:
        """A stream whose worker runs or not."""
        self._thread = SimpleNamespace(is_alive=lambda: running) if running else None
        self.available = available
        self.callback: Any = None
        self.sources: list[str] = []

    def set_update_callback(self, callback: Any) -> None:
        """Remember the callback of the worker."""
        self.callback = callback

    def update_source(self, url: str) -> None:
        """Remember the new address."""
        self.sources.append(url)


async def test_hls_stream_gets_a_fresh_address(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A stopped or broken stream gets a fresh address; a running one is left alone."""
    await setup_hub(hass, aioclient_mock, config_entry, mock_server, {})
    garage = camera_entity(hass, 14)

    async def _create(self: Camera) -> Any:
        return self.stream

    with patch.object(Camera, "async_create_stream", _create):
        garage.stream = None
        assert await garage.async_create_stream() is None

        first = FakeStream()
        garage.stream = first
        garage._source_at = None
        assert await garage.async_create_stream() is first
        assert first.sources == [HLS_URL]  # stopped and the address is old

        running = FakeStream(running=True, available=True)
        garage.stream = running
        assert await garage.async_create_stream() is running
        assert running.sources == []

        # Just asked for an address: it is fresh enough.
        assert await garage.async_create_stream() is running
        stopped = FakeStream()
        garage.stream = stopped
        assert await garage.async_create_stream() is stopped
        assert stopped.sources == []

    # The worker reports a failure: one fresh address, not more than once in 30 s.
    broken = FakeStream()
    garage.stream = broken
    garage._refresh_at = None
    garage._async_stream_updated()
    await hass.async_block_till_done()
    assert broken.sources == [HLS_URL]
    garage._async_stream_updated()
    await hass.async_block_till_done()
    assert broken.sources == [HLS_URL]
    garage.stream = FakeStream(available=True)
    garage._async_stream_updated()
    garage.stream = None
    garage._async_stream_updated()

    # No address, or the stream changed meanwhile: nothing to update.
    other = FakeStream()
    garage.stream = FakeStream()
    await garage._async_refresh_source(other)
    assert other.sources == []
    garage.hub.denied.add("live:read")
    await garage._async_refresh_source(garage.stream)
    assert garage.stream.sources == []
    garage.hub.denied.clear()
    garage.stream = None


async def test_remove_a_device_by_hand(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A shared camera or the account stays; a camera no longer shared may go."""
    unloaded = MockConfigEntry(domain=DOMAIN, unique_id="7")
    gone = SimpleNamespace(identifiers={(DOMAIN, "42_camera_99")})
    assert await async_remove_config_entry_device(hass, unloaded, gone) is True

    await setup_hub(hass, aioclient_mock, config_entry, mock_server, {})
    gate = device_of(hass, config_entry, "42_camera_12")
    account = device_of(hass, config_entry, "42_account")
    assert await async_remove_config_entry_device(hass, config_entry, gate) is False
    assert await async_remove_config_entry_device(hass, config_entry, account) is False
    assert await async_remove_config_entry_device(hass, config_entry, gone) is True


async def test_unload_stops_when_platforms_refuse(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A platform that does not unload keeps the entry running."""
    await setup_hub(hass, aioclient_mock, config_entry, mock_server, {})
    with patch.object(hass.config_entries, "async_unload_platforms", return_value=False):
        assert not await async_unload_entry(hass, config_entry)


@pytest.mark.parametrize("confidence", [True, "x", None])
def test_confidence_without_a_number(confidence: Any) -> None:
    """A flag or a word is not a measured confidence."""
    from custom_components.prostocam.words import confidence_percent

    assert confidence_percent(confidence) is None
