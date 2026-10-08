"""Cameras of ProstoCAM in Home Assistant (protocol 2)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import json
from typing import Any

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    MockLongPollSideEffect,
)

from custom_components.prostocam.const import (
    CATALOG_INTERVAL,
    DETECTION_RESET,
    DOMAIN,
    ISSUE_MISSING_ACCESS,
    SNAPSHOT_CACHE,
    VERSION,
)
from custom_components.prostocam.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.prostocam.sse import SseParser
from homeassistant.components.camera import (
    CameraEntityFeature,
    async_get_image,
    async_get_stream_source,
)
from homeassistant.config_entries import ConfigEntryState, SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.util import dt as dt_util

from .conftest import BASE, TOKEN, calls_to, server_config


STREAM_URL = "https://new.prosto.cam/v2/stream"
ALL_SCOPES = ["smart_home:ingest", "cameras:read", "events:read", "live:read"]
HLS_URL = "https://new.prosto.cam/live/cam12/hls.m3u8?token=secret-start-token-12"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-frame\xff\xd9"
CURSOR_1 = "v1.WzE3NjAwMDAwMDAwMDAsMTAsMjAsLTEsIiIsIiIsW10sIiJd"
CURSOR_2 = "v1.WzE3NjAwMDAwMTUwMDAsMTAsMjEsLTEsIiIsIiIsW10sIiJd"


def v2_config(capabilities: list[str] | None = None) -> dict[str, Any]:
    """`config` of a protocol 2 server (contract §3, §8.1)."""
    return {
        **server_config(),
        "protocol_version": 2,
        "battery_levels": True,
        "capabilities": ALL_SCOPES if capabilities is None else capabilities,
    }


def camera_item(camera_id: int, name: str, *, online: bool = True) -> dict[str, Any]:
    """One item of `GET /v2/smart-home/ha/cameras` (contract §8.2)."""
    return {
        "id": camera_id,
        "device_id": camera_id,
        "name": name,
        "location": "Front yard",
        "zone": "perimeter",
        "manufacturer": "Hikvision",
        "model": "DS-2CD2043G2-I",
        "online": online,
        "streaming": online,
        "video_state": "online" if online else "offline",
        "last_frame_at": "2026-10-08T01:00:00+03:00",
        "last_event_at": None,
        "features": {
            "live": True,
            "snapshot": True,
            "events": True,
            "ai_mode": "alarm",
            "ptz": False,
        },
        "links": {
            "snapshot": f"/v2/smart-home/ha/cameras/{camera_id}/snapshot",
            "live": f"/v2/smart-home/ha/cameras/{camera_id}/live",
            "stream": f"/v2/smart-home/ha/{{live_key}}/cameras/{camera_id}/index.m3u8",
        },
    }


def frame(event: str, data: dict[str, Any], cursor: str | None = None) -> str:
    """One SSE frame exactly as the server writes it (`SseFrame::event`)."""
    head = f"id: {cursor}\n" if cursor else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def hello(cursor: str = CURSOR_1, resumed: bool = False) -> str:
    """The first frame of a connection."""
    return frame(
        "hello",
        {
            "server_time": "2026-10-08T01:00:00+03:00",
            "cursor": cursor,
            "resumed": resumed,
            "pulse_seconds": 15,
            "retry_ms": 3000,
            "language": "uk",
        },
        cursor,
    )


def alarm_frame(
    camera_id: int = 12,
    classification: str = "person",
    *,
    test: bool = False,
    cursor: str = CURSOR_2,
) -> str:
    """`alarm` — an item of `GET /v2/notifications` (V2AlarmNotificationDTO)."""
    return frame(
        "alarm",
        {
            "alarm": {
                "id": 9001,
                "camera_id": camera_id,
                "camera_title": "Gate",
                "camera_location": "Front yard",
                "event_id": 456,
                "classification": classification,
                "confidence": 0.75,
                "created_at": "2026-10-08T01:00:05+03:00",
                "read": False,
                "handed_to": 1,
                "test": test,
            }
        },
        cursor,
    )


def status_frame(camera_id: int, state: str) -> str:
    """`camera.status` — an item of `GET /v2/cameras/status` (V2CameraStatusDTO)."""
    return frame(
        "camera.status",
        {
            "camera": {
                "id": camera_id,
                "title": "Gate",
                "video": {
                    "state": state,
                    "streaming": state == "online",
                    "since": "2026-10-08T01:00:00+03:00",
                },
                "scene": None,
                "recording": None,
                "arming": {"armed": True},
                "last_event_at": None,
                "last_frame_at": "2026-10-08T01:02:00+03:00",
            }
        },
        CURSOR_2,
    )


def register_cameras(
    aioclient_mock: AiohttpClientMocker,
    items: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    """Catalog, live and snapshot replies of the server."""
    items = [camera_item(12, "Gate"), camera_item(14, "Garage")] if items is None else items
    aioclient_mock.get(
        f"{BASE}/cameras",
        json={"data": {"items": items, "config": config or v2_config()}},
    )
    for item in items:
        aioclient_mock.get(
            f"{BASE}/cameras/{item['id']}/live",
            json={
                "data": {
                    "camera_id": item["id"],
                    "hls_url": HLS_URL,
                    "expires_at": "2026-10-08T01:05:00+03:00",
                }
            },
            headers={"Cache-Control": "no-store"},
        )
        aioclient_mock.get(
            f"{BASE}/cameras/{item['id']}/snapshot",
            content=JPEG,
            headers={"Content-Type": "image/jpeg", "X-Frame-Taken-At": "2026-10-08T01:00:05+03:00"},
        )


def device_of(
    hass: HomeAssistant, entry: MockConfigEntry, identifier: str
) -> dr.DeviceEntry | None:
    """The device of the entry with this identifier (any Home Assistant version)."""
    return next(
        (
            device
            for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            if (DOMAIN, identifier) in device.identifiers
        ),
        None,
    )


def entity_id(hass: HomeAssistant, domain: str, camera_id: int, key: str) -> str | None:
    """Entity id by unique id (entity names depend on translations)."""
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"42_{camera_id}_{key}")


async def wait_for(condition: Callable[[], bool], rounds: int = 300) -> None:
    """Let the event channel task run until `condition` holds."""
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0.01)
    assert condition()


async def setup_v2(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    *,
    capabilities: list[str] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> MockLongPollSideEffect:
    """Set up an entry against a protocol 2 server; the channel waits for replies."""
    config = v2_config(capabilities)
    mock_server(config=config)
    register_cameras(aioclient_mock, items, config)
    channel = MockLongPollSideEffect()
    aioclient_mock.get(STREAM_URL, side_effect=channel)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return channel


# ------------------------------------------------------------------ parser


def test_sse_parser_reads_server_frames() -> None:
    """Frames of `SseFrame::event`, the retry line and comments."""
    parser = SseParser()
    text = "retry: 3000\n\n" + hello() + ": keep-alive\n\n" + alarm_frame()
    events = [event for line in text.splitlines(keepends=True) if (event := parser.feed(line))]
    assert [event.event for event in events] == ["hello", "alarm"]
    assert parser.retry_ms == 3000
    assert events[0].id == CURSOR_1
    assert parser.last_event_id == CURSOR_2
    assert json.loads(events[1].data)["alarm"]["classification"] == "person"


def test_sse_parser_joins_data_lines_and_crlf() -> None:
    """Several `data:` lines are joined with a line break; CRLF is a line end."""
    parser = SseParser()
    lines = ["event: x\r\n", "data: {\"a\":\r\n", "data: 1}\r\n", "\r\n"]
    events = [event for line in lines if (event := parser.feed(line))]
    assert len(events) == 1
    assert json.loads(events[0].data) == {"a": 1}
    assert events[0].id is None


# ------------------------------------------------------------- entities


async def test_cameras_become_devices_and_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Every shared camera gets a device with a camera, sensors, an event and an image."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)

    device = device_of(hass, config_entry, "42_camera_12")
    assert device is not None
    assert device.name == "Gate"
    assert device.manufacturer == "Hikvision"
    assert device.model == "DS-2CD2043G2-I"

    camera = entity_id(hass, "camera", 12, "camera")
    assert camera is not None
    state = hass.states.get(camera)
    assert state is not None
    assert state.attributes.get("supported_features", 0) & CameraEntityFeature.STREAM
    assert state.attributes["zone"] == "perimeter"
    for key in ("motion", "person", "vehicle", "connectivity"):
        assert entity_id(hass, "binary_sensor", 12, key) is not None, key
        assert entity_id(hass, "binary_sensor", 14, key) is not None, key
    assert hass.states.get(entity_id(hass, "binary_sensor", 12, "connectivity")).state == "on"
    assert hass.states.get(entity_id(hass, "binary_sensor", 12, "motion")).state == "off"
    assert entity_id(hass, "event", 12, "alarm") is not None
    assert entity_id(hass, "image", 12, "last_alarm") is not None

    # The catalog is read with the token; the header never shows in the log.
    catalog = calls_to(aioclient_mock, "cameras")
    assert catalog and catalog[0][3]["Authorization"] == f"Bearer {TOKEN}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ACCESS) is None


async def test_stream_source_asks_for_every_start(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`stream_source()` reads a fresh address each time; snapshots are cached 10 s."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    camera = entity_id(hass, "camera", 12, "camera")

    assert await async_get_stream_source(hass, camera) == HLS_URL
    assert await async_get_stream_source(hass, camera) == HLS_URL
    assert len(calls_to(aioclient_mock, "cameras/12/live")) == 2

    image = await async_get_image(hass, camera)
    assert image.content == JPEG
    await async_get_image(hass, camera)
    assert len(calls_to(aioclient_mock, "cameras/12/snapshot")) == 1
    hub = config_entry.runtime_data.cameras
    hub.states[12].snapshot_mono -= SNAPSHOT_CACHE + 1
    await async_get_image(hass, camera)
    assert len(calls_to(aioclient_mock, "cameras/12/snapshot")) == 2


async def test_alarm_turns_on_sensors_event_and_image(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`alarm` from the channel: motion and person on for 30 s, event, alarm frame."""
    channel = await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    await wait_for(lambda: len(calls_to(aioclient_mock, "v2/stream")) == 1)
    first = calls_to(aioclient_mock, "v2/stream")[0]
    assert first[3]["Authorization"] == f"Bearer {TOKEN}"
    assert "Last-Event-ID" not in first[3]

    hub = config_entry.runtime_data.cameras
    channel.queue_response(text="retry: 3000\n\n" + hello() + alarm_frame(12, "person"))
    await wait_for(lambda: hub.stats["events"].get("alarm") == 1)
    await hass.async_block_till_done()

    motion = entity_id(hass, "binary_sensor", 12, "motion")
    person = entity_id(hass, "binary_sensor", 12, "person")
    vehicle = entity_id(hass, "binary_sensor", 12, "vehicle")
    assert hass.states.get(motion).state == "on"
    assert hass.states.get(person).state == "on"
    assert hass.states.get(vehicle).state == "off"
    assert hass.states.get(entity_id(hass, "binary_sensor", 14, "motion")).state == "off"

    event = hass.states.get(entity_id(hass, "event", 12, "alarm"))
    assert event.attributes["event_type"] == "person"
    assert event.attributes["classification"] == "person"
    assert event.attributes["confidence"] == 0.75
    assert event.attributes["event_id"] == 456
    assert event.attributes["test"] is False

    image = hass.states.get(entity_id(hass, "image", 12, "last_alarm"))
    assert image.state not in (None, "unknown", "unavailable")
    assert hub.states[12].alarm_image == JPEG
    assert hub.cursor == CURSOR_2

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DETECTION_RESET + 1))
    await hass.async_block_till_done()
    assert hass.states.get(motion).state == "off"
    assert hass.states.get(person).state == "off"


async def test_test_alarm_and_unknown_class(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A test alarm is the `test` event; a class without a type of its own is `other`."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    hub = config_entry.runtime_data.cameras
    parser = SseParser()
    for line in (alarm_frame(12, "motion", test=True) + alarm_frame(14, "intrusion")).splitlines(
        keepends=True
    ):
        if (event := parser.feed(line)) is not None:
            hub.handle_event(event)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id(hass, "event", 12, "alarm")).attributes["event_type"] == "test"
    other = hass.states.get(entity_id(hass, "event", 14, "alarm"))
    assert other.attributes["event_type"] == "other"
    assert other.attributes["classification"] == "intrusion"
    assert hass.states.get(entity_id(hass, "binary_sensor", 14, "motion")).state == "on"
    assert hass.states.get(entity_id(hass, "binary_sensor", 14, "person")).state == "off"


async def test_camera_status_drives_connectivity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`camera.status` with video state offline turns the connection sensor off."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    hub = config_entry.runtime_data.cameras
    parser = SseParser()
    for line in status_frame(12, "offline").splitlines(keepends=True):
        if (event := parser.feed(line)) is not None:
            hub.handle_event(event)
    await hass.async_block_till_done()
    connectivity = hass.states.get(entity_id(hass, "binary_sensor", 12, "connectivity"))
    assert connectivity.state == "off"
    assert connectivity.attributes["video_state"] == "offline"


async def test_channel_resumes_with_last_event_id(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`session.closed token_expired`: reconnect at once with the stored cursor."""
    channel = await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    await wait_for(lambda: len(calls_to(aioclient_mock, "v2/stream")) == 1)
    channel.queue_response(
        text=hello()
        + frame("session.closed", {"reason": "token_expired", "message": "..."}, None)
    )
    await wait_for(lambda: len(calls_to(aioclient_mock, "v2/stream")) == 2)
    second = calls_to(aioclient_mock, "v2/stream")[1]
    assert second[3]["Last-Event-ID"] == CURSOR_1
    hub = config_entry.runtime_data.cameras
    assert hub.stats["stream_last_close"] == "token_expired"


async def test_channel_401_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A revoked token on the channel asks for a new code."""
    channel = await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    await wait_for(lambda: len(calls_to(aioclient_mock, "v2/stream")) == 1)
    channel.queue_response(
        status=401, json={"code": "stream_auth_required", "message": "..."}
    )
    await wait_for(lambda: config_entry.runtime_data.auth_failed)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_only_what_the_token_may_see(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Without events and live: a camera with snapshots only, no detections, a repair issue."""
    await setup_v2(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        capabilities=["smart_home:ingest", "cameras:read"],
    )
    camera = entity_id(hass, "camera", 12, "camera")
    assert camera is not None
    assert not hass.states.get(camera).attributes.get("supported_features", 0) & CameraEntityFeature.STREAM
    assert entity_id(hass, "binary_sensor", 12, "connectivity") is not None
    assert entity_id(hass, "binary_sensor", 12, "motion") is None
    assert entity_id(hass, "event", 12, "alarm") is None
    assert entity_id(hass, "image", 12, "last_alarm") is None
    await asyncio.sleep(0.05)
    assert not calls_to(aioclient_mock, "v2/stream")
    assert await async_get_stream_source(hass, camera) is None
    assert not calls_to(aioclient_mock, "cameras/12/live")

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ACCESS)
    assert issue is not None
    assert issue.translation_placeholders == {"scopes": "events:read, live:read"}


async def test_no_camera_access_no_devices(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A token that only sends sensors: no catalog read, no devices, an issue."""
    await setup_v2(
        hass, aioclient_mock, config_entry, mock_server, capabilities=["smart_home:ingest"]
    )
    assert not calls_to(aioclient_mock, "cameras")
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ACCESS)
    assert issue.translation_placeholders == {"scopes": "cameras:read, events:read, live:read"}


async def test_protocol_1_server_has_no_cameras(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A server of protocol 1: sensors only, nothing asked about cameras, no issue."""
    mock_server()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert not [call for call in aioclient_mock.mock_calls if call[0].lower() == "get"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ACCESS) is None


async def test_granting_access_reloads(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The subscriber gives access in the web account: the next heartbeat adds entities."""
    await setup_v2(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        capabilities=["smart_home:ingest", "cameras:read"],
    )
    assert entity_id(hass, "binary_sensor", 12, "motion") is None

    aioclient_mock.clear_requests()
    config = v2_config()
    mock_server(config=config)
    register_cameras(aioclient_mock, None, config)
    aioclient_mock.get(STREAM_URL, side_effect=MockLongPollSideEffect())
    await config_entry.runtime_data.async_heartbeat()
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert entity_id(hass, "binary_sensor", 12, "motion") is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ACCESS) is None


async def test_camera_no_longer_shared_is_removed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The catalog read every 5 minutes drops the device of a camera that is gone."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    assert device_of(hass, config_entry, "42_camera_14") is not None

    aioclient_mock.clear_requests()
    config = v2_config()
    mock_server(config=config)
    register_cameras(aioclient_mock, [camera_item(12, "Gate"), camera_item(15, "Porch")], config)
    aioclient_mock.get(STREAM_URL, side_effect=MockLongPollSideEffect())
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=CATALOG_INTERVAL + 1))
    await hass.async_block_till_done()

    assert device_of(hass, config_entry, "42_camera_14") is None
    assert device_of(hass, config_entry, "42_camera_15") is not None
    assert entity_id(hass, "camera", 15, "camera") is not None
    assert entity_id(hass, "camera", 14, "camera") is None


async def test_diagnostics_hide_token_and_live_address(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Neither the token nor a live address reach diagnostics."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    camera = entity_id(hass, "camera", 12, "camera")
    assert await async_get_stream_source(hass, camera) == HLS_URL

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    dumped = json.dumps(result)
    assert TOKEN not in dumped
    assert "secret-start-token" not in dumped
    assert "live_key" not in dumped
    cameras = result["cameras"]
    assert cameras["capabilities"] == sorted(ALL_SCOPES)
    assert [camera["id"] for camera in cameras["cameras"]] == [12, 14]
    assert cameras["stats"]["live_starts"] == 1


async def test_heartbeat_reports_the_version(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The heartbeat carries the version of the integration (1.0.0, contract §14.6)."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    heartbeat = calls_to(aioclient_mock, "heartbeat")[0]
    assert heartbeat[2]["client_version"] == VERSION == "1.0.0"


async def test_event_entity_fires_bus_state_change(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Automations see the alarm as a state change of the event entity."""
    await setup_v2(hass, aioclient_mock, config_entry, mock_server)
    changes = async_capture_events(hass, "state_changed")
    hub = config_entry.runtime_data.cameras
    parser = SseParser()
    for line in alarm_frame(12, "vehicle").splitlines(keepends=True):
        if (event := parser.feed(line)) is not None:
            hub.handle_event(event)
    await hass.async_block_till_done()
    alarm = entity_id(hass, "event", 12, "alarm")
    assert any(change.data["entity_id"] == alarm for change in changes)
    assert hass.states.get(entity_id(hass, "binary_sensor", 12, "vehicle")).state == "on"
