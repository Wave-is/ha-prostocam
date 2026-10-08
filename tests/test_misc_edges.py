"""Rare paths of Media, the config and options flows, the services and the frame view."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.prostocam.config_flow import normalize_server
from custom_components.prostocam.const import (
    CONF_ARMING_SYNC,
    CONF_ARMING_SYNC_ENTITY,
    CONF_CODE,
    CONF_DEVICE_CLASSES,
    CONF_DOMAINS,
    DOMAIN,
)
from custom_components.prostocam.media_source import (
    ProstoCamMediaSource,
    async_get_media_source,
)
from homeassistant import config_entries
from homeassistant.components.media_source import MediaSourceItem, Unresolvable
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import BASE
from .test_cameras import device_of
from .test_v4 import Replies, setup_v4

PAIR_URL = f"{BASE}/pair"
SERVER_ERROR = (500, {})


def today() -> str:
    """The day of Home Assistant."""
    return dt_util.now().date().isoformat()


async def test_media_rare_paths(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Folders of a connection, of the archive and its hours; bad ids; failed reads."""
    events = Replies(
        (
            200,
            {
                "data": {
                    "items": [
                        {"id": "x"},
                        "bad",
                        {"id": 5, "test": True, "occurred_at": None, "has_frame": False},
                    ],
                    "has_more": True,
                }
            },
        ),
        SERVER_ERROR,
    )
    clip = Replies(SERVER_ERROR)

    def _before(mock: AiohttpClientMocker) -> None:
        mock.get(f"{BASE}/cameras/12/events", side_effect=events)
        mock.get(f"{BASE}/events/5/clip", side_effect=clip)

    await setup_v4(hass, aioclient_mock, config_entry, mock_server, before=_before)
    MockConfigEntry(domain=DOMAIN, unique_id="other").add_to_hass(hass)  # not loaded
    source = await async_get_media_source(hass)
    assert isinstance(source, ProstoCamMediaSource)
    entry_id = config_entry.entry_id

    def item(identifier: str) -> MediaSourceItem:
        return MediaSourceItem(hass, DOMAIN, identifier, None)

    cameras = await source.async_browse_media(item(entry_id))
    assert [child.title for child in cameras.children] == ["Garage", "Gate"]
    archive = await source.async_browse_media(item(f"{entry_id}/12/archive"))
    assert archive.children[0].identifier == f"{entry_id}/12/archive/{today()}"
    hours = await source.async_browse_media(item(f"{entry_id}/12/archive/{today()}"))
    assert all(child.can_play for child in hours.children)
    day = await source.async_browse_media(item(f"{entry_id}/12/day/{today()}"))
    assert [child.identifier for child in day.children] == [f"{entry_id}/12/event/5"]
    assert "Test alarm" in day.children[0].title
    assert len(events.bodies) == 2  # the second page failed

    for identifier in (
        f"{entry_id}/12/archive/not-a-day",
        f"{entry_id}/99",
        "nope",
        f"{entry_id}/12/zzz",
    ):
        with pytest.raises(Unresolvable):
            await source.async_browse_media(item(identifier))

    for identifier in ("a/b", f"{entry_id}/12/zzz/1"):
        with pytest.raises(Unresolvable):
            await source.async_resolve_media(item(identifier))
    with pytest.raises(Unresolvable, match="HTTP 500"):
        await source.async_resolve_media(item(f"{entry_id}/12/event/5"))
    clip.replies = [(200, {"data": {"hls_url": "rtsp://x", "ranges": []}})]
    with pytest.raises(Unresolvable, match="no clip"):
        await source.async_resolve_media(item(f"{entry_id}/12/event/5"))


def test_server_address_forms() -> None:
    """A broken address is no server; a path is kept without the last slash."""
    assert normalize_server("http://[::1") is None
    assert normalize_server("ftp://x") is None
    assert normalize_server(" https://cam.example.org/ ") == "https://cam.example.org"


@pytest.mark.parametrize(
    ("reply", "errors"),
    [
        ({"status": 409, "json": {"code": "client_outdated", "message": "x"}}, {"base": "client_outdated"}),
        ({"status": 404, "json": {"code": "pairing_code_invalid", "message": "x"}}, {CONF_CODE: "invalid_code"}),
        ({"status": 401, "json": {"code": "token_invalid", "message": "x"}}, {CONF_CODE: "invalid_code"}),
        ({"status": 500, "json": {}}, {"base": "cannot_connect"}),
        ({"status": 201, "json": {"data": {"integration_id": 5}}}, {"base": "unknown"}),
    ],
)
async def test_pairing_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    reply: dict[str, Any],
    errors: dict[str, str],
) -> None:
    """Each refusal of the pairing has its message."""
    aioclient_mock.post(PAIR_URL, **reply)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234"}
    )
    assert result["errors"] == errors


async def test_pairing_unexpected_error(hass: HomeAssistant) -> None:
    """Anything unexpected is "unknown" and logged."""
    with patch(
        "custom_components.prostocam.config_flow.ProstoCamClient.async_pair",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: "ABCD1234"}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_options_sync_needs_another_panel(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Sync needs a panel, and not the ProstoCAM panel; a chosen panel is suggested again."""
    registry = er.async_get(hass)
    own = registry.async_get_or_create(
        "alarm_control_panel", DOMAIN, "42_arming", config_entry=config_entry
    )
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_ARMING_SYNC: True, CONF_ARMING_SYNC_ENTITY: "alarm_control_panel.ajax"}
    )
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    base = {CONF_DOMAINS: ["binary_sensor"], CONF_DEVICE_CLASSES: ["door"], CONF_ARMING_SYNC: True}
    result = await hass.config_entries.options.async_configure(result["flow_id"], base)
    assert result["errors"] == {CONF_ARMING_SYNC_ENTITY: "sync_entity_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, CONF_ARMING_SYNC_ENTITY: own.entity_id}
    )
    assert result["errors"] == {CONF_ARMING_SYNC_ENTITY: "sync_entity_own"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, CONF_ARMING_SYNC_ENTITY: "alarm_control_panel.ajax"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_services_rare_paths(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Mute and test alarm by device; no event; a response nobody asked for; wrong devices."""
    mute = Replies((200, {"data": {"camera_id": 12, "muted": True, "muted_until": "2026-10-08T02:00:00+03:00"}}))
    test_alarm = Replies((202, {"data": {"camera_id": 12, "event_id": 777}}))
    verify = Replies((200, {"data": {"status": "verified", "event_id": 456, "camera_id": 12, "secret": "x"}}))

    def _before(mock: AiohttpClientMocker) -> None:
        mock.put(f"{BASE}/cameras/12/mute", side_effect=mute)
        mock.post(f"{BASE}/cameras/12/test-alarm", side_effect=test_alarm)
        mock.post(f"{BASE}/events/456/ai-verify", side_effect=verify)

    await setup_v4(hass, aioclient_mock, config_entry, mock_server, before=_before)
    gate = device_of(hass, config_entry, "42_camera_12").id
    await hass.services.async_call(DOMAIN, "mute", {"device_id": gate, "minutes": 30}, blocking=True)
    assert mute.bodies == [{"minutes": 30}]
    await hass.services.async_call(DOMAIN, "test_alarm", {"device_id": gate}, blocking=True)
    assert len(test_alarm.bodies) == 1
    await hass.services.async_call(
        DOMAIN, "verify_ai", {"event_id": 456, "spend_credit": True}, blocking=True
    )
    assert len(verify.bodies) == 1
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "verify_ai", {"spend_credit": False}, blocking=True)
    assert err.value.translation_key == "no_event"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "arm_anyway", {"state": "armed_away", "config_entry_id": "nope"}, blocking=True
        )
    assert err.value.translation_key == "entry_not_loaded"

    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    stranger = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other", "1")}
    )
    for device_id in (stranger.id, "missing"):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN, "test_alarm", {"device_id": device_id}, blocking=True
            )
        assert err.value.translation_key == "not_a_camera"

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "arm_anyway", {"state": "armed_away"}, blocking=True)
    assert err.value.translation_key == "choose_entry"


async def test_services_need_the_actions_area(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Without actions:write the camera services refuse; without arming:write arm_anyway does."""
    scopes = ["smart_home:ingest", "cameras:read", "events:read", "live:read", "archive:read"]
    await setup_v4(hass, aioclient_mock, config_entry, mock_server, capabilities=scopes)
    gate = device_of(hass, config_entry, "42_camera_12").id
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "test_alarm", {"device_id": gate}, blocking=True)
    assert err.value.translation_placeholders == {"scope": "actions:write"}
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "arm_anyway", {"state": "armed_away"}, blocking=True)
    assert err.value.translation_placeholders == {"scope": "arming:write"}


async def test_frame_view_refuses_odd_requests(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    hass_client: Any,
) -> None:
    """Not a number, an unknown connection, no frame: 404."""
    frames = Replies((404, {"code": "event_frame_missing", "message": "x"}))
    await setup_v4(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        before=lambda mock: mock.get(f"{BASE}/events/77/snapshot", side_effect=frames),
    )
    client = await hass_client()
    entry_id = config_entry.entry_id
    for path in (
        f"/api/prostocam/{entry_id}/events/x/snapshot.jpg",
        f"/api/prostocam/nope/events/77/snapshot.jpg",
        f"/api/prostocam/{entry_id}/events/77/snapshot.jpg",
    ):
        response = await client.get(path)
        assert response.status == 404, path
