"""Refusals and rare paths of the arming, account, mute, deterrence and AI check (protocol 3)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.prostocam.api import ProstoCamRejectedError
from custom_components.prostocam.const import (
    CONF_ARMING_SYNC,
    CONF_ARMING_SYNC_ENTITY,
    TRIGGERED_HOLD,
)
from custom_components.prostocam.control import _int
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .conftest import BASE
from .test_v3 import arming
from .test_v4 import Replies, setup_v4

PANEL = "alarm_control_panel.house"
SERVER_ERROR = (500, {})


async def setup_routes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    routes: dict[tuple[str, str], Replies],
    **kwargs: Any,
) -> Any:
    """Set up a protocol 4 entry; `routes` answer before the usual replies."""

    def _before(mock: AiohttpClientMocker) -> None:
        for (method, path), replies in routes.items():
            getattr(mock, method)(f"{BASE}{path}", side_effect=replies)

    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, before=_before, **kwargs)
    return hub.control


def test_int_reads_numbers_only() -> None:
    """Numbers and digit strings; a bool or a word is no number."""
    assert _int(3) == 3
    assert _int("12") == 12
    assert _int(True) is None
    assert _int("x") is None


async def test_command_errors_name_the_reason(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A lost area, a revoked token, a refusal with words, a server that fails."""
    test_alarm = Replies(
        (403, {"code": "scope_missing", "message": "actions:write"}),
        (409, {"code": "rate_limited_camera", "message": "Раз на хвилину"}),
        (409, {"code": "busy"}),
        SERVER_ERROR,
        (401, {"code": "token_invalid", "message": "x"}),
    )
    control = await setup_routes(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        {("post", "/cameras/12/test-alarm"): test_alarm},
    )
    keys = []
    for _ in range(5):
        with pytest.raises(HomeAssistantError) as err:
            await control.async_test_alarm(12)
        keys.append(err.value.translation_key)
    assert keys == ["scope_missing", "refused", "request_failed", "request_failed", "token_invalid"]
    assert "actions:write" in control.cameras.denied
    await hass.async_block_till_done()
    assert control.bridge.auth_failed


async def test_arming_reads_ticks_and_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The safety read, a read that fails, a state without a mode, a stopped control."""
    reads = Replies((200, {"data": arming("armed_away", 3)}))
    control = await setup_routes(
        hass, aioclient_mock, config_entry, mock_server, {("get", "/arming"): reads}
    )
    count = len(reads.bodies)

    # The channel is down: the tick reads; up and fresh: it does not.
    control.cameras.stats["stream_connected"] = False
    await control._async_arming_tick(None)
    assert len(reads.bodies) == count + 1
    control.cameras.stats["stream_connected"] = True
    await control._async_arming_tick(None)
    assert len(reads.bodies) == count + 1

    reads.replies = [SERVER_ERROR]
    assert await control.async_refresh_arming() is None
    assert control.stats["arming_errors"] == 1

    control.bridge.auth_failed = True
    assert await control.async_refresh_arming() is None
    control.bridge.auth_failed = False

    control._stopped = True
    control.handle_arming_changed({"revision": 9})
    control._stopped = False

    before = control.arming
    control._apply_arming({"state": None})
    assert control.arming is before

    with pytest.raises(HomeAssistantError) as err:
        await control.async_set_arming("armed_vacation")
    assert err.value.translation_key == "arming_state_unknown"


async def test_triggered_ends_by_itself(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A test alarm or a disarmed system is no trigger; a real one ends after two minutes."""
    reads = Replies((200, {"data": arming("armed_away", 3)}))
    control = await setup_routes(
        hass, aioclient_mock, config_entry, mock_server, {("get", "/arming"): reads}
    )
    control.note_alarm(test=True)
    assert control.arming_state == "armed_away"

    control.cameras.denied.add("arming:write")
    control.note_alarm(test=False)
    assert control.arming_state == "armed_away"
    control.cameras.denied.discard("arming:write")

    control.note_alarm(test=False)
    control.note_alarm(test=False)
    assert control.arming_state == "triggered"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=TRIGGERED_HOLD + 5))
    await hass.async_block_till_done()
    assert control._triggered_unsub is None


async def test_arming_command_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Another refusal keeps the words of the server; a failed server is "not reached"."""
    commands = Replies((409, {"code": "arming_locked", "message": "Заблоковано"}), SERVER_ERROR)
    control = await setup_routes(
        hass, aioclient_mock, config_entry, mock_server, {("post", "/arming"): commands}
    )
    with pytest.raises(HomeAssistantError) as err:
        await control.async_set_arming("armed_away")
    assert err.value.translation_key == "refused"
    with pytest.raises(HomeAssistantError) as err:
        await control.async_set_arming("armed_away")
    assert err.value.translation_key == "request_failed"
    assert control.stats["arming_refusals"] == {"arming_locked": 1}


async def test_arm_anyway_tries_once_more_with_the_new_list(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The list of cameras changed between the read and the command: one more try."""
    readiness = {
        "state": "armed_away",
        "verdict": "not_ready",
        "reasons": ["camera_offline"],
        "not_ready": [
            {"camera_id": 14, "name": "Garage", "why": "camera_offline"},
            {"camera_id": "x"},
            "bad",
            {"camera_id": 12, "name": "", "why": 5},
        ],
    }
    reads = Replies((200, {"data": arming("disarmed", 2, readiness=readiness)}))
    commands = Replies(
        (409, {"code": "arming_uncovered_not_acknowledged", "message": "Назвіть", "uncovered": [14, 16, "y"]}),
        (200, {"data": {"result": "applied", "command_id": 7, "arming": arming("armed_away", 3)}}),
    )
    control = await setup_routes(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        {("get", "/arming"): reads, ("post", "/arming"): commands},
    )
    result = await control.async_arm_anyway("armed_away")
    assert result["result"] == "applied"
    assert commands.bodies[-1]["acknowledged_uncovered"] == [14, 16]

    # No list in the refusal: the refusal itself.
    commands.replies = [(409, {"code": "arming_not_ready", "message": "Не готово"})]
    with pytest.raises(HomeAssistantError):
        await control.async_arm_anyway("armed_away")

    # The readiness can not be read: the cameras of the refusal still count.
    reads.replies = [SERVER_ERROR]
    refusal = ProstoCamRejectedError(409, "arming_not_ready", detail={"uncovered": [14, 14, "z"]})
    cameras = await control._not_ready_cameras("armed_away", refusal)
    assert cameras == [(14, "Garage", None)]


async def test_sync_mirrors_to_the_ha_panel(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """ProstoCAM → panel of HA: by its service; a panel that refuses is only logged."""
    hass.states.async_set(PANEL, "disarmed")
    commands = Replies(SERVER_ERROR)
    control = await setup_routes(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        {("post", "/arming"): commands},
    )
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_ARMING_SYNC: True, CONF_ARMING_SYNC_ENTITY: PANEL}
    )
    await hass.async_block_till_done()
    control = config_entry.runtime_data.cameras.control
    armed = async_mock_service(hass, "alarm_control_panel", "alarm_arm_away")

    async def _refuse(call: ServiceCall) -> None:
        raise HomeAssistantError("needs a code")

    hass.services.async_register("alarm_control_panel", "alarm_arm_home", _refuse)

    control.arming = arming("disarmed", 2)
    control._apply_arming(arming("armed_away", 3, source="telegram"))
    await hass.async_block_till_done()
    assert len(armed) == 1
    assert armed[0].data["entity_id"] == PANEL

    control._apply_arming(arming("armed_home", 4, source="schedule"))
    await hass.async_block_till_done()  # refused by the panel: a warning only

    control._apply_arming(arming("arming", 5, pending={"state": "armed_night"}))
    hass.states.async_set(PANEL, "unavailable")
    control._apply_arming(arming("armed_night", 6))
    await hass.async_block_till_done()
    assert len(armed) == 1

    # Panel → ProstoCAM: a removed panel, a mode ProstoCAM is going to, a failed command.
    hass.states.async_set(PANEL, "disarmed")
    await hass.async_block_till_done()
    hass.states.async_remove(PANEL)
    await hass.async_block_till_done()
    control.arming = arming("arming", 7, pending={"state": "armed_night"})
    hass.states.async_set(PANEL, "armed_home")
    await hass.async_block_till_done()
    hass.states.async_set(PANEL, "armed_night")
    await hass.async_block_till_done()
    control._mirrored = None
    control.arming = arming("disarmed", 8)
    hass.states.async_set(PANEL, "armed_away")
    await hass.async_block_till_done()
    assert control.stats["sync_to_prostocam"] >= 1


async def test_account_reads_and_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Billing silent (503) keeps the numbers; other failures count; a lost token stops."""
    account = Replies((200, {"data": {"connected": True, "stage": "active", "ai_credits": 3}}))
    control = await setup_routes(
        hass, aioclient_mock, config_entry, mock_server, {("get", "/account"): account}
    )
    account.replies = [(503, {"code": "billing_unavailable", "message": "x"})]
    await control._async_account_tick(None)
    assert control.account["ai_credits"] == 3
    account.replies = [(403, {"code": "scope_missing", "message": "account:read"})]
    await control.async_refresh_account()
    assert control.stats["account_errors"] == 2
    assert "account:read" in control.cameras.denied
    control.cameras.denied.clear()
    control.bridge.auth_failed = True
    calls_before = len(account.bodies)
    await control.async_refresh_account()
    assert len(account.bodies) == calls_before
    control.bridge.auth_failed = False


async def test_mute_and_deterrence_failures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """Timers read every camera; failed reads count, failed commands raise."""
    mute = Replies((200, {"data": {"camera_id": 12, "muted": False, "muted_until": None}}))
    set_mute = Replies(SERVER_ERROR)
    outputs = Replies((200, {"data": {"camera_id": 12, "capable": True, "actions": ["siren", 5]}}))
    deter = Replies((409, {"code": "deterrence_too_late", "message": "Тривога застара"}))
    control = await setup_routes(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        {
            ("get", "/cameras/12/mute"): mute,
            ("put", "/cameras/12/mute"): set_mute,
            ("get", "/cameras/12/deterrence"): outputs,
            ("post", "/cameras/12/deterrence"): deter,
        },
    )
    assert control.cameras.states[12].deter_actions == ["siren"]
    reads = len(mute.bodies)
    await control._async_mute_tick(None)
    await control._async_deterrence_tick(None)
    assert len(mute.bodies) == reads + 1

    mute.replies = [SERVER_ERROR]
    outputs.replies = [SERVER_ERROR]
    await control._async_read_mute(12)
    await control._async_read_deterrence(12)
    assert control.stats["mute_errors"] == 1
    assert control.stats["deterrence_errors"] == 1

    control.bridge.auth_failed = True
    await control._async_read_mute(12)
    await control._async_read_deterrence(12)
    assert control.stats["mute_errors"] == 1
    control.bridge.auth_failed = False

    with pytest.raises(HomeAssistantError) as err:
        await control.async_set_mute(12, 30)
    assert err.value.translation_key == "request_failed"
    with pytest.raises(HomeAssistantError) as err:
        await control.async_deter(12)
    assert err.value.translation_placeholders == {"message": "Тривога застара"}


async def test_ai_check_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """No alarm yet, a refusal, a failed server; the button passes such errors on."""
    verify = Replies((402, {"code": "ai_credits_insufficient", "message": "Кредитів немає"}), SERVER_ERROR)
    control = await setup_routes(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        {("post", "/events/456/ai-verify"): verify},
    )
    with pytest.raises(HomeAssistantError) as err:
        await control.async_verify_ai(None, None, spend_credit=False)
    assert err.value.translation_key == "no_event"
    with pytest.raises(HomeAssistantError) as err:
        await control.async_verify_ai(None, 456, spend_credit=True)
    assert err.value.translation_placeholders == {"message": "Кредитів немає"}
    control.cameras.states[12].last_event_id = 456
    with pytest.raises(HomeAssistantError) as err:
        await control.async_press_ai(12)
    assert err.value.translation_key == "request_failed"
