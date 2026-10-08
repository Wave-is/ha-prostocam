"""Errors of actions and buttons: every known code of the server has a phrase (1.0.1).

A refusal of the server is a `ServiceValidationError` (Home Assistant answers
400 and writes no trace); only a server that can not be reached is a
`HomeAssistantError`. Unknown codes get a general phrase and are counted in
diagnostics.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import json
from pathlib import Path
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.prostocam.api import (
    ProstoCamAuthError,
    ProstoCamConnectionError,
    ProstoCamRateLimitedError,
    ProstoCamRejectedError,
    ProstoCamScopeError,
    ProstoCamUnavailableError,
    raise_for_reply,
)
from custom_components.prostocam.const import DOMAIN
from custom_components.prostocam.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.prostocam.errors import (
    EXPORT_KEYS,
    INVALID_CODES,
    REFUSAL_KEYS,
    command_error,
    export_error,
    recorded_ranges,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .conftest import BASE
from .test_cameras import device_of
from .test_v4 import Replies, export, job, setup_export, setup_v4

COMPONENT = Path(__file__).parent.parent / "custom_components" / DOMAIN
LANGUAGES = ("en", "uk", "ru", "bg")


def rejected(status: int, code: str | None, message: str | None = None, **detail: Any) -> ProstoCamRejectedError:
    """The refusal the client makes of a reply of the server."""
    body: dict[str, Any] = {"code": code, **detail}
    if message is not None:
        body["message"] = message
    with pytest.raises(ProstoCamRejectedError) as err:
        raise_for_reply(status, json.dumps(body), {})
    return err.value


def exceptions_of(name: str) -> dict[str, str]:
    """`exceptions` of strings.json or of a translation: key → message."""
    path = COMPONENT / name
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: value["message"] for key, value in data["exceptions"].items()}


# ------------------------------------------------------------ every code


@pytest.mark.parametrize(("code", "key"), sorted(REFUSAL_KEYS.items()))
def test_every_known_refusal_has_its_phrase(code: str, key: str) -> None:
    """A known code → its own key, a validation error (no trace), the code counted."""
    codes: dict[str, int] = {}
    err = command_error(rejected(409, code, "server words"), codes)
    assert type(err) is ServiceValidationError
    assert err.translation_domain == DOMAIN
    assert err.translation_key == key
    assert codes == {code: 1}


@pytest.mark.parametrize("code", sorted(INVALID_CODES))
def test_invalid_requests_name_the_field(code: str) -> None:
    """The phrase of the server names the refused field; without it — the field, the code."""
    err = command_error(rejected(422, code, "Поле from у майбутньому"))
    assert err.translation_key == "invalid_request"
    assert err.translation_placeholders == {"message": "Поле from у майбутньому"}
    with pytest.raises(ProstoCamRejectedError) as raw:
        raise_for_reply(422, json.dumps({"error": {"code": code, "field": "duration_s"}}), {})
    assert command_error(raw.value).translation_placeholders == {"message": "duration_s"}
    assert command_error(rejected(400, code)).translation_placeholders == {"message": code}


@pytest.mark.parametrize(("code", "key"), sorted(EXPORT_KEYS.items()))
def test_every_export_failure_has_its_phrase(code: str, key: str) -> None:
    """Every code of the export worker → a phrase; the code goes to diagnostics."""
    codes: dict[str, int] = {}
    err = export_error(code, codes)
    assert type(err) is ServiceValidationError
    assert err.translation_key == key
    assert codes == {code: 1}


def test_unknown_codes_get_a_general_phrase() -> None:
    """An unknown refusal: the words of the server or a general phrase; never the raw code."""
    codes: dict[str, int] = {}
    err = command_error(rejected(409, "deterrence_too_late", "Тривога застара"), codes)
    assert err.translation_key == "refused"
    assert err.translation_placeholders == {"message": "Тривога застара"}
    err = command_error(rejected(409, "brand_new_code"), codes)
    assert type(err) is ServiceValidationError
    assert err.translation_key == "refused_unknown"
    assert err.translation_placeholders is None
    err = command_error(rejected(409, None), codes)
    assert err.translation_key == "refused_unknown"
    assert codes == {"deterrence_too_late": 1, "brand_new_code": 1, "none": 1}

    err = export_error("media.something_new", codes)
    assert err.translation_key == "export_failed"
    assert err.translation_placeholders is None
    assert codes["media.something_new"] == 1
    assert export_error(None).translation_key == "export_failed"


def test_access_token_limits_and_unreachable_server() -> None:
    """Scope, token, 429, 503 with a code are refusals; a lost server is a real error."""
    codes: dict[str, int] = {}
    err = command_error(ProstoCamScopeError("scope_missing", "archive:read"), codes)
    assert type(err) is ServiceValidationError
    assert (err.translation_key, err.translation_placeholders) == (
        "scope_missing",
        {"message": "archive:read"},
    )
    assert command_error(ProstoCamScopeError("scope_missing", None)).translation_placeholders == {
        "message": "scope_missing"
    }
    err = command_error(ProstoCamAuthError("HTTP 401"), codes)
    assert type(err) is ServiceValidationError
    assert err.translation_key == "token_invalid"

    with pytest.raises(ProstoCamRateLimitedError) as limited:
        raise_for_reply(429, json.dumps({"code": "rate_limited"}), {})
    err = command_error(limited.value, codes)
    assert type(err) is ServiceValidationError
    assert err.translation_key == "rate_limited"

    err = command_error(ProstoCamUnavailableError("billing_unavailable", 30), codes)
    assert type(err) is ServiceValidationError
    assert err.translation_key == "server_busy"
    assert command_error(ProstoCamUnavailableError("webrtc_unavailable", None)).translation_key == "server_busy"

    err = command_error(ProstoCamConnectionError("ClientConnectorError"), codes)
    assert type(err) is HomeAssistantError
    assert err.translation_key == "request_failed"
    assert err.translation_placeholders == {"error": "ClientConnectorError"}
    with pytest.raises(ProstoCamConnectionError) as failed:
        raise_for_reply(502, json.dumps({"code": "bad_gateway"}), {})
    assert type(command_error(failed.value, codes)) is HomeAssistantError
    assert codes == {
        "scope_missing": 1,
        "token_invalid": 1,
        "rate_limited": 1,
        "billing_unavailable": 1,
        "ProstoCamConnectionError": 1,
        "bad_gateway": 1,
    }
    assert command_error(ProstoCamConnectionError("x")).translation_key == "request_failed"


def test_recorded_ranges_read_only_numbers() -> None:
    """Neighbours of an empty window: numbers only, a missing side is no side."""
    assert recorded_ranges(
        {
            "recorded_before": {"from": 100, "duration": 72},
            "recorded_after": {"from": 900, "duration": 40},
        }
    ) == [(100, 72), (900, 40)]
    assert recorded_ranges({"recorded_before": {"from": True, "duration": 1}}) == []
    assert recorded_ranges({"recorded_after": {"from": 1, "duration": -1}}) == []
    assert recorded_ranges({"recorded_after": "x"}) == []
    assert recorded_ranges({}) == []


# --------------------------------------------------------- translations


def _keys_raised_in_code() -> set[str]:
    keys = set(REFUSAL_KEYS.values()) | set(EXPORT_KEYS.values())
    keys |= {
        "export_failed",
        "export_no_recording_nearest",
        "invalid_request",
        "refused",
        "refused_unknown",
        "request_failed",
        "rate_limited",
        "server_busy",
        "scope_missing",
        "token_invalid",
    }
    for path in COMPONENT.glob("*.py"):
        keys |= set(re.findall(r'translation_key="([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    return keys


def test_every_phrase_exists_in_every_language() -> None:
    """strings.json and uk/ru/en/bg: every raised key, the same placeholders, no raw code."""
    english = exceptions_of("strings.json")
    assert exceptions_of("translations/en.json") == english
    raised = _keys_raised_in_code()
    assert raised <= set(english), raised - set(english)
    for language in LANGUAGES:
        phrases = exceptions_of(f"translations/{language}.json")
        assert set(phrases) == set(english), language
        for key, message in phrases.items():
            assert set(re.findall(r"\{(\w+)\}", message)) == set(
                re.findall(r"\{(\w+)\}", english[key])
            ), (language, key)
        for key in set(EXPORT_KEYS.values()) | {"export_failed", "refused_unknown"}:
            assert "{" not in phrases[key] or key == "export_no_recording_nearest"
            assert "plan_empty" not in phrases[key]
    assert "{nearest}" in exceptions_of("translations/uk.json")["export_no_recording_nearest"]
    assert "запису немає" in exceptions_of("translations/uk.json")["export_no_recording"]
    assert "ШІ" in exceptions_of("translations/uk.json")["ai_credits_insufficient"]


# ------------------------------------------------------ through the actions


async def test_export_without_recording_names_the_nearest_recording(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`multipart.plan_empty` (1402): no 500, the phrase and the recordings around the clip."""
    before = int((dt_util.now() - timedelta(days=2)).timestamp())
    after = int(dt_util.now().timestamp()) - 30
    archive = Replies(
        (
            404,
            {
                "code": "no_recording",
                "message": "За цей проміжок запису немає",
                "recorded_before": {"from": before, "duration": 72},
                "recorded_after": {"from": after, "duration": 20},
            },
        )
    )
    order = Replies((202, job("queued")))
    status = Replies((200, job("failed", error_code="multipart.plan_empty")))
    hub = await setup_export(
        hass, aioclient_mock, config_entry, mock_server, order, status, monkeypatch, archive
    )
    past = (dt_util.now() - timedelta(hours=1)).isoformat()

    with pytest.raises(ServiceValidationError) as err:
        await export(hass, past)
    assert err.value.translation_key == "export_no_recording_nearest"

    def _clock(start: int, seconds: int) -> str:
        first = dt_util.as_local(dt_util.utc_from_timestamp(start))
        last = dt_util.as_local(dt_util.utc_from_timestamp(start + seconds))
        day = "" if first.date() == dt_util.now().date() else first.strftime("%d.%m ")
        return f"{day}{first:%H:%M:%S}–{last:%H:%M:%S}"

    assert err.value.translation_placeholders == {
        "nearest": f"{_clock(before, 72)}, {_clock(after, 20)}"
    }
    assert "." in _clock(before, 72)  # two days ago: with the date
    # The window asked is the window of the job.
    asked = [call for call in aioclient_mock.mock_calls if call[1].path.endswith("/cameras/12/archive")]
    assert asked[-1][1].query["from"] == str(
        int(dt_util.parse_datetime(job()["data"]["from"]).timestamp())
    )
    assert hub.archive.stats["exports_failed"] == 1
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["cameras"]["archive"]["stats"]["error_codes"] == {"multipart.plan_empty": 1}

    # Recorded by now, the archive does not answer, no neighbours: the phrase alone.
    for reply in (
        (200, {"data": {"camera_id": 12, "hls_url": "https://x/y.m3u8", "ranges": [{"from": 1, "duration": 2}]}}),
        (500, {}),
        (404, {"code": "no_recording", "message": "Запису немає"}),
        (404, {"code": "camera_not_found", "message": "Камери немає"}),
    ):
        archive.replies = [reply]
        with pytest.raises(ServiceValidationError) as err:
            await export(hass, past)
        assert err.value.translation_key == "export_no_recording"
        assert err.value.translation_placeholders is None

    # A job without its window: nothing to ask.
    status.replies = [(200, job("failed", error_code="no_recording", **{"from": None}))]
    calls = len(archive.bodies)
    with pytest.raises(ServiceValidationError) as err:
        await export(hass, past)
    assert err.value.translation_key == "export_no_recording"
    assert len(archive.bodies) == calls

    # Another reason: no question to the archive at all.
    status.replies = [(200, job("failed", error_code="media.source_compatibility"))]
    with pytest.raises(ServiceValidationError) as err:
        await export(hass, past)
    assert err.value.translation_key == "export_encoding_changed"
    assert len(archive.bodies) == calls


async def test_actions_answer_refusals_in_words(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """mute, test alarm, AI check, arming anyway, the question: a refusal is a phrase."""
    mute = Replies((404, {"code": "camera_not_found", "message": "Камери немає"}))
    test_alarm = Replies((429, {"code": "rate_limited", "message": "Раз на хвилину"}))
    verify = Replies((402, {"code": "ai_credits_insufficient", "message": "Кредитів немає"}))
    arm = Replies((409, {"code": "arming_readiness_unknown", "message": "Невідомо"}))
    ask = Replies((403, {"code": "account_inactive", "message": "Акаунт призупинено"}))

    def _before(mock: AiohttpClientMocker) -> None:
        mock.put(f"{BASE}/cameras/12/mute", side_effect=mute)
        mock.post(f"{BASE}/cameras/12/test-alarm", side_effect=test_alarm)
        mock.post(f"{BASE}/events/456/ai-verify", side_effect=verify)
        mock.post(f"{BASE}/arming", side_effect=arm)
        mock.post(f"{BASE}/ask", side_effect=ask)

    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, before=_before)
    gate = device_of(hass, config_entry, "42_camera_12").id
    calls: list[tuple[str, dict[str, Any], str]] = [
        ("mute", {"device_id": gate, "minutes": 30}, "camera_not_found"),
        ("test_alarm", {"device_id": gate}, "rate_limited"),
        ("verify_ai", {"event_id": 456, "spend_credit": True}, "ai_credits_insufficient"),
        ("arm_anyway", {"state": "armed_away"}, "arming_readiness_unknown"),
    ]
    for service, data, key in calls:
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, service, data, blocking=True)
        assert err.value.translation_key == key, service
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "ask_archive", {"question": "хто був?"}, blocking=True, return_response=True
        )
    assert err.value.translation_key == "account_inactive"

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    control_codes = diagnostics["cameras"]["control"]["stats"]["error_codes"]
    assert control_codes["camera_not_found"] == 1
    assert control_codes["rate_limited"] == 1
    assert control_codes["ai_credits_insufficient"] == 1
    assert control_codes["arming_readiness_unknown"] >= 1
    assert hub.archive.stats["error_codes"] == {"account_inactive": 1}
