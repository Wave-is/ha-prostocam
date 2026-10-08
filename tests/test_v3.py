"""Protocol 3 of ProstoCAM in Home Assistant: media, arming, buttons, account (contract §11–§12).

The server double answers with the bodies of the contract (`V2SmartHomeHa*DTO`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
    MockLongPollSideEffect,
)
import voluptuous as vol

from custom_components.prostocam.const import (
    CONF_ARMING_SYNC,
    CONF_ARMING_SYNC_ENTITY,
    DOMAIN,
    EVENT_AI_VERDICT,
    EVENT_ALARM,
    ISSUE_ACCOUNT_STAGE,
    ISSUE_MISSING_ACCESS,
)
from custom_components.prostocam.control import _price_phrase
from custom_components.prostocam.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.prostocam.media_source import ProstoCamMediaSource
from custom_components.prostocam.select import minutes_until_morning
from custom_components.prostocam.sse import SseParser
from homeassistant.components.media_source import MediaSourceItem, Unresolvable
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util

from .conftest import BASE, TOKEN
from .test_cameras import (
    ALL_SCOPES,
    JPEG,
    STREAM_URL,
    alarm_frame,
    frame,
    register_cameras,
    v2_config,
)

CONTROL_SCOPES = ["archive:read", "arming:write", "actions:write", "account:read"]
V3_SCOPES = ALL_SCOPES + CONTROL_SCOPES
EVENT_JPEG = b"\xff\xd8\xff\xe0event-frame-456\xff\xd9"
CLIP_URL = "https://new.prosto.cam/v2/media/cam12/index-1759886404-40.m3u8?token=secret-clip-token"
ACCOUNT = {
    "connected": True,
    "stage": "active",
    "balance_minor": 15000,
    "currency": "UAH",
    "tariff": "Базовий",
    "next_fee_minor": 9900,
    "next_charge_date": "2026-11-01",
    "paid_until": "2026-10-31",
    "ai_credits": 37,
    "ai_credits_included": 50,
}

Register = Callable[[AiohttpClientMocker], None]


def v3_config(capabilities: list[str] | None = None) -> dict[str, Any]:
    """`config` of a protocol 3 server."""
    return {**v2_config(V3_SCOPES if capabilities is None else capabilities), "protocol_version": 3}


def arming(
    state: str = "disarmed",
    revision: int = 2,
    *,
    source: str = "prostocam",
    pending: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`V2SmartHomeHaArmingDTO`."""
    modes = {"disarmed": "disarmed", "armed_home": "home", "armed_night": "night", "armed_away": "away"}
    body: dict[str, Any] = {
        "state": state,
        "mode": modes.get(state, "disarmed"),
        "armed": state not in ("disarmed", "arming"),
        "revision": revision,
        "changed_at": "2026-10-08T04:00:00+03:00",
        "changed_by": "telegram",
        "source": source,
        "pending": pending,
        "echo_window_s": 0,
        "supported_states": ["disarmed", "armed_home", "armed_night", "armed_away"],
    }
    if readiness is not None:
        body["readiness"] = readiness
    return body


def calls(aioclient_mock: AiohttpClientMocker, method: str, path: str) -> list[tuple]:
    """Requests of one method to one path (the query does not count)."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method and call[1].path == f"/v2/smart-home/ha{path}"
    ]


def register_v3(aioclient_mock: AiohttpClientMocker, account: dict[str, Any] | None = None) -> None:
    """Arming, account, mute and deterrence replies (camera 12 has a siren, 14 has none)."""
    aioclient_mock.get(f"{BASE}/arming", json={"data": arming()})
    aioclient_mock.get(f"{BASE}/account", json={"data": account or ACCOUNT})
    for camera_id in (12, 14):
        aioclient_mock.get(
            f"{BASE}/cameras/{camera_id}/mute",
            json={"data": {"camera_id": camera_id, "muted": False, "muted_until": None}},
        )
        aioclient_mock.get(
            f"{BASE}/cameras/{camera_id}/deterrence",
            json={
                "data": {
                    "camera_id": camera_id,
                    "capable": camera_id == 12,
                    "actions": ["siren"] if camera_id == 12 else [],
                }
            },
        )
    aioclient_mock.get(
        f"{BASE}/events/456/snapshot",
        content=EVENT_JPEG,
        headers={"Content-Type": "image/jpeg", "X-Frame-Taken-At": "2026-10-08T01:00:05+03:00"},
    )


async def setup_v3(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    *,
    capabilities: list[str] | None = None,
    before: Register | None = None,
    account: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockLongPollSideEffect:
    """Set up an entry against a protocol 3 server; `before` registers replies that win."""
    if options is not None:
        hass.config_entries.async_update_entry(config_entry, options=options)
    if before is not None:
        before(aioclient_mock)
    config = v3_config(capabilities)
    mock_server(config=config)
    register_cameras(aioclient_mock, None, config)
    register_v3(aioclient_mock, account)
    channel = MockLongPollSideEffect()
    aioclient_mock.get(STREAM_URL, side_effect=channel)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return channel


def entity(hass: HomeAssistant, domain: str, unique_id: str) -> str | None:
    """Entity id by unique id."""
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


def feed(hub: Any, text: str) -> None:
    """Apply SSE frames to the hub as the channel would."""
    parser = SseParser()
    for line in text.splitlines(keepends=True):
        if (event := parser.feed(line)) is not None:
            hub.handle_event(event)


def alarm_with_links(event_id: int = 456, confidence: float = 0.91) -> str:
    """`alarm` of the integration channel with the links of protocol 3."""
    return frame(
        "alarm",
        {
            "alarm": {
                "id": 9001,
                "camera_id": 12,
                "camera_title": "Gate",
                "camera_location": "Front yard",
                "event_id": event_id,
                "classification": "person",
                "confidence": confidence,
                "created_at": "2026-10-08T01:00:05+03:00",
                "read": False,
                "test": False,
            },
            "links": {
                "snapshot": f"/v2/smart-home/ha/events/{event_id}/snapshot",
                "clip": f"/v2/smart-home/ha/events/{event_id}/clip",
            },
        },
        "v1.cursor-3",
    )


# ------------------------------------------------------------- entities


async def test_v3_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """All areas: arming panel, buttons, do not disturb, account sensors; deter only with a siren."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)

    panel = entity(hass, "alarm_control_panel", "42_arming")
    assert panel is not None
    state = hass.states.get(panel)
    assert state.state == "disarmed"
    assert state.attributes["revision"] == 2
    assert state.attributes["changed_by"] == "telegram"

    for camera_id in (12, 14):
        assert entity(hass, "button", f"42_{camera_id}_test_alarm") is not None
        assert entity(hass, "select", f"42_{camera_id}_do_not_disturb") is not None
        ai = entity(hass, "button", f"42_{camera_id}_ai_check")
        assert ai is not None
        # Spending credits is never on by default.
        assert er.async_get(hass).async_get(ai).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entity(hass, "button", "42_12_deter") is not None
    assert entity(hass, "button", "42_14_deter") is None
    dnd = hass.states.get(entity(hass, "select", "42_12_do_not_disturb"))
    assert dnd.state == "off"

    balance = hass.states.get(entity(hass, "sensor", "42_account_balance"))
    assert float(balance.state) == 150.0
    assert balance.attributes["unit_of_measurement"] == "UAH"
    assert hass.states.get(entity(hass, "sensor", "42_account_tariff")).state == "Базовий"
    assert hass.states.get(entity(hass, "sensor", "42_account_ai_credits")).state == "37"
    assert hass.states.get(entity(hass, "sensor", "42_account_next_charge")).state == "2026-11-01"
    assert hass.states.get(entity(hass, "sensor", "42_account_stage")).state == "active"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ACCESS) is None

    # The reads carry the token; it never leaves the header.
    assert calls(aioclient_mock, "get", "/arming")[0][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_v3_without_new_areas(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Protocol 3 with the areas of protocol 2 only: nothing new is created or asked, a repair names them."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server, capabilities=ALL_SCOPES)
    assert entity(hass, "alarm_control_panel", "42_arming") is None
    assert entity(hass, "button", "42_12_test_alarm") is None
    assert entity(hass, "select", "42_12_do_not_disturb") is None
    assert entity(hass, "sensor", "42_account_balance") is None
    assert entity(hass, "camera", "42_12_camera") is not None
    for path in ("/arming", "/account", "/cameras/12/mute", "/cameras/12/deterrence"):
        assert not calls(aioclient_mock, "get", path), path
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ACCESS)
    assert issue.translation_placeholders == {"scopes": ", ".join(CONTROL_SCOPES)}


async def test_arming_without_cameras(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Arming and account without cameras:read: the panel and sensors exist, no camera."""
    await setup_v3(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        capabilities=["smart_home:ingest", "arming:write", "account:read"],
    )
    assert entity(hass, "alarm_control_panel", "42_arming") is not None
    assert entity(hass, "sensor", "42_account_balance") is not None
    assert entity(hass, "camera", "42_12_camera") is None


# ------------------------------------------------------------ alarm frame


async def test_alarm_image_is_the_event_frame(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`alarm` with links: the image is the saved frame of event 456, not "now"; the bus hears it."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    alarms = async_capture_events(hass, EVENT_ALARM)
    hub = config_entry.runtime_data.cameras
    feed(hub, alarm_with_links())
    await hass.async_block_till_done()

    assert hub.states[12].alarm_image == EVENT_JPEG
    assert hub.states[12].alarm_image_source == "event"
    assert calls(aioclient_mock, "get", "/events/456/snapshot")
    assert not calls(aioclient_mock, "get", "/cameras/12/snapshot")
    image = hass.states.get(entity(hass, "image", "42_12_last_alarm"))
    assert image.attributes["event_id"] == 456
    assert image.attributes["frame"] == "event"

    path = f"/api/prostocam/{config_entry.entry_id}/events/456/snapshot.jpg"
    event = hass.states.get(entity(hass, "event", "42_12_alarm"))
    assert event.attributes["snapshot"] == path
    assert event.attributes["media_content_id"].endswith(f"/{config_entry.entry_id}/12/event/456")
    assert len(alarms) == 1
    data = alarms[0].data
    assert data["camera_name"] == "Gate"
    assert data["event_type"] == "person"
    assert data["snapshot"] == path
    assert data["device_id"] is not None
    assert TOKEN not in json.dumps(data)


async def test_alarm_without_saved_frame_takes_now(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """No saved frame (404 event_frame_missing twice): the frame "now", marked as such."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.get(
            f"{BASE}/events/457/snapshot",
            status=404,
            json={"code": "event_frame_missing", "message": "Кадру немає"},
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    hub = config_entry.runtime_data.cameras
    with patch("custom_components.prostocam.cameras.FRAME_RETRY", 0):
        feed(hub, alarm_with_links(457))
        await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "get", "/events/457/snapshot")) == 2
    assert hub.states[12].alarm_image == JPEG
    assert hub.states[12].alarm_image_source == "now"


async def test_frame_view_needs_a_login(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    hass_client: Any,
    hass_client_no_auth: Any,
) -> None:
    """The frame proxy gives the event frame to a logged-in user only, 404 for anything else."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    path = f"/api/prostocam/{config_entry.entry_id}/events/456/snapshot.jpg"

    client = await hass_client()
    response = await client.get(path)
    assert response.status == 200
    assert await response.read() == EVENT_JPEG
    assert response.headers["Content-Type"].startswith("image/jpeg")
    missing = await client.get("/api/prostocam/no-such-entry/events/456/snapshot.jpg")
    assert missing.status == 404

    anonymous = await hass_client_no_auth()
    assert (await anonymous.get(path)).status == 401


# ------------------------------------------------------------------ arming


async def test_arming_command_carries_the_revision(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """«Нікого немає» from HA: POST with expected_revision; the answer is the new state."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(
            f"{BASE}/arming",
            json={
                "data": {
                    "result": "applied",
                    "command_id": 17,
                    "exit_seconds": 0,
                    "arming": arming("armed_away", 3, source="ha"),
                }
            },
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    panel = entity(hass, "alarm_control_panel", "42_arming")
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": panel}, blocking=True
    )
    posts = calls(aioclient_mock, "post", "/arming")
    assert posts[-1][2] == {"state": "armed_away", "expected_revision": 2}
    state = hass.states.get(panel)
    assert state.state == "armed_away"
    assert state.attributes["source"] == "ha"
    assert state.attributes["revision"] == 3


async def test_arming_not_ready_names_the_cameras(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """409 arming_not_ready: an error and a notification with the cameras and why."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(
            f"{BASE}/arming",
            status=409,
            json={"code": "arming_not_ready", "message": "Не всі камери готові", "uncovered": [14]},
        )
        mock.get(
            f"{BASE}/arming?state=armed_away",
            json={
                "data": arming(
                    readiness={
                        "state": "armed_away",
                        "verdict": "not_ready",
                        "reasons": ["camera_offline"],
                        "not_ready": [{"camera_id": 14, "name": "Garage", "why": "camera_offline"}],
                    }
                )
            },
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    panel = entity(hass, "alarm_control_panel", "42_arming")
    with (
        patch(
            "custom_components.prostocam.control.persistent_notification.async_create"
        ) as notify,
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_away", {"entity_id": panel}, blocking=True
        )
    assert err.value.translation_key == "arming_not_ready"
    assert err.value.translation_placeholders == {"cameras": "Garage (camera_offline)"}
    assert notify.call_count == 1
    assert "Garage (camera_offline)" in notify.call_args[0][1]
    assert hass.states.get(panel).state == "disarmed"


async def test_arm_anyway_acknowledges_each_camera(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`prostocam.arm_anyway`: policy degraded, every not ready camera named."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.get(
            f"{BASE}/arming?state=armed_away",
            json={
                "data": arming(
                    readiness={
                        "state": "armed_away",
                        "verdict": "not_ready",
                        "reasons": ["camera_offline"],
                        "not_ready": [{"camera_id": 14, "name": "Garage", "why": "camera_offline"}],
                    }
                )
            },
        )
        mock.post(
            f"{BASE}/arming",
            json={
                "data": {
                    "result": "applied",
                    "command_id": 18,
                    "exit_seconds": 0,
                    "arming": arming("armed_away", 3, source="ha"),
                }
            },
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    await hass.services.async_call(DOMAIN, "arm_anyway", {"state": "armed_away"}, blocking=True)
    assert calls(aioclient_mock, "post", "/arming")[-1][2] == {
        "state": "armed_away",
        "policy": "degraded",
        "acknowledged_uncovered": [14],
        "expected_revision": 2,
    }


async def test_stale_command_rereads_and_does_not_repeat(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """409 version_conflict: the state is read again, the command is not repeated."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(
            f"{BASE}/arming",
            status=409,
            json={"code": "version_conflict", "message": "Стан змінився", "revision": 5},
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    reads = len(calls(aioclient_mock, "get", "/arming"))
    panel = entity(hass, "alarm_control_panel", "42_arming")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_home", {"entity_id": panel}, blocking=True
        )
    assert err.value.translation_key == "arming_conflict"
    assert len(calls(aioclient_mock, "post", "/arming")) == 1
    assert len(calls(aioclient_mock, "get", "/arming")) == reads + 1


async def test_arming_changed_on_the_channel(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`arming.changed` with a new revision reads the state; the known revision does not."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    hub = config_entry.runtime_data.cameras
    reads = len(calls(aioclient_mock, "get", "/arming"))
    feed(hub, frame("arming.changed", {"arming": {"armed": False, "mode": "disarmed", "revision": 2, "pending": None}}))
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "get", "/arming")) == reads
    feed(hub, frame("arming.changed", {"arming": {"armed": True, "mode": "away", "revision": 9, "pending": None}}))
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "get", "/arming")) == reads + 1


async def test_alarm_while_armed_triggers_the_panel(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A real alarm while armed: `triggered`; a test alarm: no."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    control = config_entry.runtime_data.cameras.control
    control._apply_arming(arming("armed_away", 3))  # noqa: SLF001
    panel = entity(hass, "alarm_control_panel", "42_arming")
    hub = config_entry.runtime_data.cameras
    feed(hub, alarm_frame(12, "motion", test=True))
    await hass.async_block_till_done()
    assert hass.states.get(panel).state == "armed_away"
    feed(hub, alarm_with_links())
    await hass.async_block_till_done()
    assert hass.states.get(panel).state == "triggered"


# ------------------------------------------------------------ sync with HA


async def test_sync_ha_panel_to_prostocam(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The HA panel armed away: ProstoCAM follows with the revision; a mode it has already: no command."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(
            f"{BASE}/arming",
            json={
                "data": {
                    "result": "applied",
                    "command_id": 19,
                    "exit_seconds": 0,
                    "arming": arming("armed_away", 3, source="ha"),
                }
            },
        )

    hass.states.async_set("alarm_control_panel.ajax", "disarmed")
    await setup_v3(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        before=before,
        options={CONF_ARMING_SYNC: True, CONF_ARMING_SYNC_ENTITY: "alarm_control_panel.ajax"},
    )
    hass.states.async_set("alarm_control_panel.ajax", "arming")
    await hass.async_block_till_done()
    assert not calls(aioclient_mock, "post", "/arming")
    hass.states.async_set("alarm_control_panel.ajax", "armed_away")
    await hass.async_block_till_done()
    posts = calls(aioclient_mock, "post", "/arming")
    assert [call[2] for call in posts] == [{"state": "armed_away", "expected_revision": 2}]
    # ProstoCAM marks the change `source: ha`: it is not mirrored back.
    control = config_entry.runtime_data.cameras.control
    assert control.stats["sync_to_home_assistant"] == 0


async def test_sync_prostocam_to_ha_panel_without_loop(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Armed from Telegram: the HA panel follows; its echo is not sent back to ProstoCAM."""
    hass.states.async_set("alarm_control_panel.ajax", "disarmed")
    await setup_v3(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        options={CONF_ARMING_SYNC: True, CONF_ARMING_SYNC_ENTITY: "alarm_control_panel.ajax"},
    )
    control = config_entry.runtime_data.cameras.control
    with patch.object(control, "_async_call_panel") as call_panel:
        control._apply_arming(arming("armed_away", 3, source="prostocam"))  # noqa: SLF001
        await hass.async_block_till_done()
        assert call_panel.call_args[0] == ("alarm_control_panel.ajax", "armed_away")
        # A change this Home Assistant made itself is never mirrored.
        control._apply_arming(arming("armed_home", 4, source="ha"))  # noqa: SLF001
        await hass.async_block_till_done()
        assert call_panel.call_count == 1
    # The panel lands in the mode we set: that is our echo, not a command.
    hass.states.async_set("alarm_control_panel.ajax", "armed_away")
    await hass.async_block_till_done()
    assert not calls(aioclient_mock, "post", "/arming")
    assert control.stats["sync_echo_skipped"] == 1


async def test_sync_is_off_by_default(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Without the option nothing follows anything."""
    hass.states.async_set("alarm_control_panel.ajax", "disarmed")
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    hass.states.async_set("alarm_control_panel.ajax", "armed_away")
    await hass.async_block_till_done()
    assert not calls(aioclient_mock, "post", "/arming")
    assert config_entry.runtime_data.cameras.control.sync_entity is None


async def test_own_panel_is_never_sent_to_prostocam(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The bridge does not list our own entities (panel, detections) as sensors of HA."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    bridge = config_entry.runtime_data
    listed = {item["entity_id"] for item in bridge.build_catalog()}
    own = {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    }
    assert entity(hass, "alarm_control_panel", "42_arming") in own
    assert not listed & own


# ----------------------------------------------------------------- buttons


async def test_test_alarm_button(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """«Перевірити тривогу» posts the test alarm of its camera."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(
            f"{BASE}/cameras/12/test-alarm",
            status=202,
            json={"data": {"camera_id": 12, "event_id": 777, "occurred_at": "…", "test": True, "delivery": "queued"}},
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    await hass.services.async_call(
        "button", "press", {"entity_id": entity(hass, "button", "42_12_test_alarm")}, blocking=True
    )
    assert len(calls(aioclient_mock, "post", "/cameras/12/test-alarm")) == 1


async def test_deter_after_an_alarm(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """«Відлякати» names the last alarm event of the camera."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(
            f"{BASE}/cameras/12/deterrence",
            json={"data": {"camera_id": 12, "event_id": 456, "action": "siren", "triggered_at": "…", "detail": "ok", "now": {}}},
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    feed(config_entry.runtime_data.cameras, alarm_with_links())
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {"entity_id": entity(hass, "button", "42_12_deter")}, blocking=True
    )
    assert calls(aioclient_mock, "post", "/cameras/12/deterrence")[0][2] == {"event_id": 456}


async def _ai_reply(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
    """The server: no credit without `spend_credit: true`, a verdict with it."""
    if isinstance(data, dict) and data.get("spend_credit") is True:
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "data": {
                    "status": "verified",
                    "event_id": 456,
                    "camera_id": 12,
                    "incident_id": None,
                    "classification": "person",
                    "confidence": 0.93,
                    "reason": None,
                    "series_wait_seconds": None,
                    "attempt_id": 5001,
                    "replayed": False,
                    "words": "Людина біля хвіртки",
                }
            },
        )
    return AiohttpClientMockResponse(
        method,
        url,
        status=409,
        json={"code": "ai_credit_confirmation_required", "message": "Розбір коштує 1 кредит"},
    )


async def test_ai_check_spends_only_on_the_second_press(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """First press: the price and nothing spent; a second press within 30 s: the check."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(f"{BASE}/events/456/ai-verify", side_effect=_ai_reply)

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    control = config_entry.runtime_data.cameras.control
    feed(config_entry.runtime_data.cameras, alarm_with_links())
    await hass.async_block_till_done()
    verdicts = async_capture_events(hass, EVENT_AI_VERDICT)

    with pytest.raises(HomeAssistantError) as err:
        await control.async_press_ai(12)
    assert err.value.translation_key == "ai_press_again"
    assert err.value.translation_placeholders["message"] == "Розбір коштує 1 кредит"
    first = calls(aioclient_mock, "post", "/events/456/ai-verify")
    assert first[0][2] == {}
    assert not verdicts

    await control.async_press_ai(12)
    await hass.async_block_till_done()
    second = calls(aioclient_mock, "post", "/events/456/ai-verify")[-1]
    assert second[2] == {"spend_credit": True}
    assert second[3]["Idempotency-Key"]
    assert verdicts[0].data["words"] == "Людина біля хвіртки"
    assert verdicts[0].data["camera_name"] == "Gate"


async def test_verify_ai_service_needs_spend_credit(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`prostocam.verify_ai`: the field is required; with it the reply comes back."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.post(f"{BASE}/events/456/ai-verify", side_effect=_ai_reply)

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "verify_ai", {"event_id": 456}, blocking=True, return_response=True
        )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "verify_ai", {"event_id": 456, "spend_credit": False}, blocking=True
        )
    assert err.value.translation_key == "ai_confirm"
    result = await hass.services.async_call(
        DOMAIN,
        "verify_ai",
        {"event_id": 456, "spend_credit": True},
        blocking=True,
        return_response=True,
    )
    assert result["status"] == "verified"
    assert result["words"] == "Людина біля хвіртки"


async def test_do_not_disturb(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """«Не турбувати»: an hour = PUT 60 minutes; off = DELETE; the service takes minutes."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.put(
            f"{BASE}/cameras/12/mute",
            json={"data": {"camera_id": 12, "muted": True, "muted_until": "2026-10-08T02:00:00+03:00"}},
        )
        mock.delete(
            f"{BASE}/cameras/12/mute",
            json={"data": {"camera_id": 12, "muted": False, "muted_until": None}},
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    select = entity(hass, "select", "42_12_do_not_disturb")
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "1h"}, blocking=True
    )
    assert calls(aioclient_mock, "put", "/cameras/12/mute")[0][2] == {"minutes": 60}
    state = hass.states.get(select)
    assert state.state == "1h"
    assert state.attributes["muted_until"] == "2026-10-08T02:00:00+03:00"

    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "off"}, blocking=True
    )
    assert len(calls(aioclient_mock, "delete", "/cameras/12/mute")) == 1
    assert hass.states.get(select).state == "off"

    hub = config_entry.runtime_data.cameras
    device = hub.device_of(12)
    assert device is not None
    device_id = device.id
    await hass.services.async_call(
        DOMAIN, "mute", {"device_id": device_id, "minutes": 90}, blocking=True
    )
    assert calls(aioclient_mock, "put", "/cameras/12/mute")[-1][2] == {"minutes": 90}
    assert hass.states.get(select).state == "muted"


def test_until_the_morning() -> None:
    """«До ранку» = to the next 07:00 of local time."""
    evening = datetime(2026, 10, 8, 22, 30)
    assert minutes_until_morning(evening) == 8 * 60 + 30
    night = datetime(2026, 10, 9, 3, 0)
    assert minutes_until_morning(night) == 4 * 60
    morning = datetime(2026, 10, 9, 7, 0)
    assert minutes_until_morning(morning) == 1440


# ----------------------------------------------------------------- account


async def test_restricted_account_raises_a_repair(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Stage restricted: a repair names it; the sensor shows it."""
    await setup_v3(
        hass, aioclient_mock, config_entry, mock_server, account={**ACCOUNT, "stage": "restricted"}
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ACCOUNT_STAGE)
    assert issue is not None
    assert issue.translation_placeholders == {"stage": "restricted"}
    assert hass.states.get(entity(hass, "sensor", "42_account_stage")).state == "restricted"


# -------------------------------------------------------------------- media


def _today() -> str:
    return dt_util.now().date().isoformat()


async def test_media_browse_and_play(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """ProstoCAM → camera → day → events with frames; a clip plays from a fresh address."""

    def before(mock: AiohttpClientMocker) -> None:
        mock.get(
            f"{BASE}/cameras/12/events",
            json={
                "data": {
                    "camera_id": 12,
                    "from": "…",
                    "to": "…",
                    "page": 1,
                    "has_more": False,
                    "items": [
                        {
                            "id": 456,
                            "camera_id": 12,
                            "camera_name": "Gate",
                            "occurred_at": "2026-10-08T04:20:14+03:00",
                            "type": "motion",
                            "verdict": "person",
                            "verdict_confidence": 0.91,
                            "test": False,
                            "has_frame": True,
                            "links": {
                                "snapshot": "/v2/smart-home/ha/events/456/snapshot",
                                "clip": "/v2/smart-home/ha/events/456/clip",
                            },
                        }
                    ],
                }
            },
        )
        mock.get(
            f"{BASE}/events/456/clip",
            json={
                "data": {
                    "camera_id": 12,
                    "event_id": 456,
                    "hls_url": CLIP_URL,
                    "expires_at": "…",
                    "from": "…",
                    "to": "…",
                    "ranges": [{"from": 1759886404, "duration": 40}],
                }
            },
        )
        mock.get(
            f"{BASE}/events/458/clip",
            status=404,
            json={"code": "clip_unavailable", "message": "Кліп не замовлено", "reason": "clip_not_ordered"},
        )
        mock.get(
            f"{BASE}/cameras/12/archive",
            json={
                "data": {
                    "camera_id": 12,
                    "event_id": None,
                    "hls_url": CLIP_URL,
                    "expires_at": "…",
                    "from": "…",
                    "to": "…",
                    "ranges": [],
                }
            },
        )

    await setup_v3(hass, aioclient_mock, config_entry, mock_server, before=before)
    source = ProstoCamMediaSource(hass)
    entry_id = config_entry.entry_id

    root = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, "", None))
    assert [child.title for child in root.children] == ["Garage", "Gate"]

    camera = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, f"{entry_id}/12", None))
    assert camera.children[-1].identifier == f"{entry_id}/12/archive"

    day = await source.async_browse_media(
        MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/day/{_today()}", None)
    )
    assert len(day.children) == 1
    event = day.children[0]
    assert event.identifier == f"{entry_id}/12/event/456"
    assert event.can_play
    assert event.thumbnail == f"/api/prostocam/{entry_id}/events/456/snapshot.jpg"
    assert "91 %" in event.title
    query = calls(aioclient_mock, "get", "/cameras/12/events")[0][1].query
    assert set(query) == {"from", "to", "page", "limit"}

    played = await source.async_resolve_media(MediaSourceItem(hass, DOMAIN, event.identifier, None))
    assert played.url == CLIP_URL
    assert played.mime_type == "application/vnd.apple.mpegurl"
    # Every playback asks for a fresh address.
    await source.async_resolve_media(MediaSourceItem(hass, DOMAIN, event.identifier, None))
    assert len(calls(aioclient_mock, "get", "/events/456/clip")) == 2

    with pytest.raises(Unresolvable, match="Кліп не замовлено"):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/event/458", None)
        )
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/archive/{_today()}/00", None)
        )


async def test_media_without_archive_area(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Without archive:read events are listed but not playable, and there is no archive folder."""
    await setup_v3(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        capabilities=ALL_SCOPES,
    )
    source = ProstoCamMediaSource(hass)
    camera = await source.async_browse_media(
        MediaSourceItem(hass, DOMAIN, f"{config_entry.entry_id}/12", None)
    )
    assert all("archive" not in child.identifier for child in camera.children)
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{config_entry.entry_id}/12/event/456", None)
        )


# -------------------------------------------------------------- diagnostics


async def test_diagnostics_hold_no_secrets_or_money(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """No token, no clip address, no balance; the arming state is there."""
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    feed(config_entry.runtime_data.cameras, alarm_with_links())
    await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    dumped = json.dumps(result, ensure_ascii=False)
    assert TOKEN not in dumped
    assert "secret-clip-token" not in dumped
    assert "15000" not in dumped
    control = result["cameras"]["control"]
    assert control["arming"]["state"] == "disarmed"
    assert control["account_stage"] == "active"


async def test_heartbeat_and_protocol(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A protocol 3 server is no news for 0.3: no "update" warning."""
    with patch("custom_components.prostocam.bridge.LOGGER") as logger:
        await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    assert not any("update ProstoCAM" in str(call) for call in logger.warning.call_args_list)


# ------------------------------------------------------- 0.3.1: language and labels


async def test_requests_speak_the_language_of_home_assistant(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Every request carries `Accept-Language` of HA: the server phrases come in it."""
    hass.config.language = "ru"
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    assert calls(aioclient_mock, "get", "/arming")[0][3]["Accept-Language"] == "ru"
    assert calls(aioclient_mock, "get", "/account")[0][3]["Accept-Language"] == "ru"

    hass.config.language = "pt-BR"
    await config_entry.runtime_data.cameras.control.async_refresh_account()
    # A language the server does not speak: English, never the platform default.
    assert calls(aioclient_mock, "get", "/account")[-1][3]["Accept-Language"] == "en"


async def test_alarm_label_has_the_confidence_only_when_measured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """«Человек · 91 %» from the AI; a camera detection (confidence 0) has no percent."""
    hass.config.language = "ru"
    await setup_v3(hass, aioclient_mock, config_entry, mock_server)
    alarms = async_capture_events(hass, EVENT_ALARM)
    hub = config_entry.runtime_data.cameras
    feed(hub, alarm_with_links(456, 0.91))
    await hass.async_block_till_done()
    event = hass.states.get(entity(hass, "event", "42_12_alarm"))
    assert event.attributes["label"] == "Человек · 91 %"
    assert alarms[-1].data["label"] == "Человек · 91 %"

    feed(hub, alarm_with_links(456, 0.0))
    await hass.async_block_till_done()
    event = hass.states.get(entity(hass, "event", "42_12_alarm"))
    assert event.attributes["label"] == "Человек"
    assert alarms[-1].data["label"] == "Человек"


def test_price_phrase_drops_the_wording_of_the_api() -> None:
    """A server before 1372 named the request field; the button never shows it."""
    old = "Перевірка ШІ витрачає ШІ-кредити (1): надішліть spend_credit: true, щоб підтвердити"
    assert _price_phrase(old) == "Перевірка ШІ витрачає ШІ-кредити (1)"
    assert _price_phrase("Перевірка ШІ коштує 1 кредит(ів)") == "Перевірка ШІ коштує 1 кредит(ів)"
    assert _price_phrase(None) == ""
