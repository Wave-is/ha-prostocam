"""Protocol 4 of ProstoCAM in Home Assistant: own WebRTC, ask the archive, export a clip (contract §13–§14).

The server double answers with the bodies of the contract (`V2SmartHomeHa*DTO`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import aiohttp
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

from custom_components.prostocam import const
from custom_components.prostocam.archive import AskArchiveIntentHandler
from custom_components.prostocam.cameras import WebRtcFailed
from custom_components.prostocam.const import (
    DOMAIN,
    EVENT_EXPORT_READY,
    INTENT_ASK_ARCHIVE,
    WEBRTC_RETRY,
)
from custom_components.prostocam.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.prostocam.media_source import ProstoCamMediaSource
from custom_components.prostocam.words import events_noun
from homeassistant.components.camera import StreamType
from homeassistant.components.media_source import MediaSourceItem, Unresolvable
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import intent
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .conftest import BASE
from .test_cameras import STREAM_URL, camera_item, register_cameras
from .test_v3 import V3_SCOPES, calls, entity, register_v3, v3_config

OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
ANSWER = "v=0\r\no=- 3 4 IN IP4 203.0.113.7\r\ns=-\r\na=ice-lite\r\nm=video 8000 UDP/TLS/RTP/SAVPF 96\r\n"
DOWNLOAD = "/v1/archive/export/download/one-time-secret"


def v4_config(capabilities: list[str] | None = None) -> dict[str, Any]:
    """`config` of a protocol 4 server."""
    return {**v3_config(capabilities), "protocol_version": 4}


def v4_items(webrtc_12: bool = True) -> list[dict[str, Any]]:
    """Camera 12 has WebRTC (with a STUN server named), camera 14 does not."""
    gate = camera_item(12, "Gate")
    gate["features"]["webrtc"] = webrtc_12
    gate["links"]["webrtc"] = "/v2/smart-home/ha/cameras/12/webrtc" if webrtc_12 else None
    gate["ice_servers"] = [
        {"urls": "stun:stun.example.org:3478"},
        {"urls": ["turn:turn.example.org"], "username": "u", "credential": "c"},
        {"urls": []},
        {"urls": 5},
        "bad",
    ]
    garage = camera_item(14, "Garage")
    garage["features"]["webrtc"] = False
    return [gate, garage]


class Replies:
    """A queue of replies of one address; the last one repeats."""

    def __init__(self, *replies: tuple[int, Any]) -> None:
        """Remember the replies: (status, json) or an exception to raise."""
        self.replies = list(replies)
        self.bodies: list[Any] = []

    def push(self, *replies: tuple[int, Any]) -> None:
        """Add replies to the queue."""
        self.replies += replies

    async def __call__(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        """Answer one request."""
        self.bodies.append(data)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            return AiohttpClientMockResponse(method, url, **reply)
        status, body = reply
        return AiohttpClientMockResponse(method, url, status=status, json=body)


async def setup_v4(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    *,
    capabilities: list[str] | None = None,
    protocol: int = 4,
    webrtc: Replies | None = None,
    before: Callable[[AiohttpClientMocker], None] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> Any:
    """Set up an entry against a protocol 4 server; returns the camera hub."""
    if before is not None:
        before(aioclient_mock)
    aioclient_mock.post(
        f"{BASE}/cameras/12/webrtc",
        side_effect=webrtc or Replies((200, {"data": {"camera_id": 12, "type": "answer", "sdp": ANSWER}})),
    )
    config = {**v4_config(capabilities), "protocol_version": protocol}
    mock_server(config=config)
    register_cameras(aioclient_mock, v4_items() if items is None else items, config)
    register_v3(aioclient_mock)
    aioclient_mock.get(STREAM_URL, side_effect=MockLongPollSideEffect())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return config_entry.runtime_data.cameras


def camera_entity(hass: HomeAssistant, camera_id: int) -> Any:
    """The camera entity object (its class tells WebRTC from HLS)."""
    entity_id = entity(hass, "camera", f"42_{camera_id}_camera")
    assert entity_id is not None
    return hass.data["camera"].get_entity(entity_id)


async def offer(hass: HomeAssistant, hass_ws_client: Any, entity_id: str) -> list[dict[str, Any]]:
    """Send an offer as the frontend does; the messages of the subscription."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "camera/webrtc/offer", "entity_id": entity_id, "offer": OFFER}
    )
    reply = await client.receive_json()
    assert reply["success"], reply
    session = await client.receive_json()
    assert session["event"]["type"] == "session"
    answer = await client.receive_json()
    return [session["event"], answer["event"]]


# ------------------------------------------------------------------ WebRTC


async def test_webrtc_camera_talks_to_the_media_node(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    hass_ws_client: Any,
) -> None:
    """A camera with `features.webrtc` offers WebRTC only; the offer goes to the server as it is."""
    replies = Replies((200, {"data": {"camera_id": 12, "type": "answer", "sdp": ANSWER}}))
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, webrtc=replies)

    gate = camera_entity(hass, 12)
    garage = camera_entity(hass, 14)
    assert gate.camera_capabilities.frontend_stream_types == {StreamType.WEB_RTC}
    assert garage.camera_capabilities.frontend_stream_types == {StreamType.HLS}
    assert hass.states.get(gate.entity_id).attributes["webrtc"] is True
    assert hub.webrtc_built == {12: True, 14: False}

    session, answer = await offer(hass, hass_ws_client, gate.entity_id)
    assert answer == {"type": "answer", "answer": ANSWER}
    assert replies.bodies == [{"sdp": OFFER}]
    sent = calls(aioclient_mock, "post", "/cameras/12/webrtc")
    assert sent[0][3]["Authorization"].startswith("Bearer ")
    assert hub.stats["webrtc_answers"] == 1

    # Candidates of the browser are not needed; closing the card ends nothing at the server.
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "camera/webrtc/candidate",
            "entity_id": gate.entity_id,
            "session_id": session["session_id"],
            "candidate": {"candidate": "candidate:1 1 udp 1 192.0.2.1 5000 typ host", "sdpMLineIndex": 0},
        }
    )
    assert (await client.receive_json())["success"]
    gate.close_webrtc_session(session["session_id"])
    assert len(calls(aioclient_mock, "post", "/cameras/12/webrtc")) == 1

    # ICE servers named by the server reach the browser (Home Assistant adds its own).
    await client.send_json_auto_id(
        {"type": "camera/webrtc/get_client_config", "entity_id": gate.entity_id}
    )
    config = (await client.receive_json())["result"]["configuration"]
    urls = [server["urls"] for server in config["iceServers"]]
    assert "stun:stun.example.org:3478" in str(urls)
    assert "turn:turn.example.org" in str(urls)

    # Recording and stills keep the HLS address and the snapshot.
    assert await gate.stream_source() is not None


async def test_webrtc_unavailable_falls_back_to_hls_until_the_catalog(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    hass_ws_client: Any,
) -> None:
    """503 webrtc_unavailable: an error to the card, the camera is built anew as HLS."""
    replies = Replies((503, {"code": "webrtc_unavailable", "message": "WebRTC вимкнено"}))
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, webrtc=replies)
    gate = camera_entity(hass, 12)

    _, error = await offer(hass, hass_ws_client, gate.entity_id)
    assert error["type"] == "error"
    assert error["code"] == "webrtc_unavailable"
    assert "HLS" in error["message"]
    await hass.async_block_till_done()

    rebuilt = camera_entity(hass, 12)
    assert rebuilt is not gate
    assert rebuilt.entity_id == gate.entity_id
    assert rebuilt.camera_capabilities.frontend_stream_types == {StreamType.HLS}
    assert hub.webrtc_built[12] is False
    assert hub.stats["last_webrtc_error"] == "webrtc_unavailable"

    # The next catalog tries WebRTC again.
    await hub.async_refresh_catalog()
    await hass.async_block_till_done()
    assert camera_entity(hass, 12).camera_capabilities.frontend_stream_types == {
        StreamType.WEB_RTC
    }

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diagnostics)
    assert "v=0" not in text
    assert "ice-lite" not in text
    assert "turn.example.org" not in text
    gate_diag = next(item for item in diagnostics["cameras"]["cameras"] if item["id"] == 12)
    assert gate_diag["webrtc"] is True
    assert diagnostics["cameras"]["stats"]["webrtc_errors"] == 1


async def test_webrtc_negotiation_failed_keeps_hls_for_an_hour(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """502 (H.265, a silent node): HLS for an hour, then WebRTC again with the catalog."""
    replies = Replies((502, {"code": "webrtc_negotiation_failed", "message": "Вузол мовчить"}))
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, webrtc=replies)

    with pytest.raises(WebRtcFailed) as err:
        await hub.async_webrtc_answer(12, OFFER)
    assert err.value.code == "webrtc_negotiation_failed"
    assert err.value.fallback is True
    await hass.async_block_till_done()
    assert hub.webrtc_built[12] is False

    await hub.async_refresh_catalog()
    await hass.async_block_till_done()
    assert hub.webrtc_built[12] is False

    now = hub._clock()
    hub._clock = lambda: now + WEBRTC_RETRY + 1
    await hub.async_refresh_catalog()
    await hass.async_block_till_done()
    assert hub.webrtc_built[12] is True
    assert 12 not in hub.webrtc_off_until


@pytest.mark.parametrize(
    ("reply", "code", "fallback"),
    [
        ((404, {"code": "camera_not_found", "message": "Немає"}), "camera_not_found", False),
        ((422, {"code": "invalid_body", "message": "sdp"}), "invalid_body", True),
        ((429, {"code": "ha_live_limit", "message": "16"}), "ha_live_limit", False),
        ((200, {"data": {"camera_id": 12, "type": "answer", "sdp": ""}}), "webrtc_negotiation_failed", True),
        (aiohttp.ClientError("lost"), "webrtc_offer_failed", True),
    ],
)
async def test_webrtc_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    reply: Any,
    code: str,
    fallback: bool,
) -> None:
    """Which refusals fall back to HLS: not where HLS would fail as well."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, webrtc=Replies(reply))
    with pytest.raises(WebRtcFailed) as err:
        await hub.async_webrtc_answer(12, OFFER)
    assert err.value.code == code
    assert err.value.fallback is fallback
    assert (12 in hub.webrtc_off_until) is fallback


async def test_webrtc_token_and_scope_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """403 scope_missing stops live video; 401 starts reauth; no fallback either way."""
    replies = Replies((403, {"code": "scope_missing", "message": "live:read"}))
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, webrtc=replies)
    with pytest.raises(WebRtcFailed) as err:
        await hub.async_webrtc_answer(12, OFFER)
    assert err.value.code == "scope_missing"
    assert "live:read" in hub.denied
    # Without the area nothing is asked any more.
    with pytest.raises(WebRtcFailed) as err:
        await hub.async_webrtc_answer(12, OFFER)
    assert err.value.code == "no_access"
    assert len(replies.bodies) == 1

    hub.denied.clear()
    replies.replies = [(401, {"code": "token_invalid", "message": "x"})]
    with pytest.raises(WebRtcFailed) as err:
        await hub.async_webrtc_answer(12, OFFER)
    assert err.value.code == "token_invalid"
    await hass.async_block_till_done()
    assert hub.bridge.auth_failed


async def test_protocol_3_server_keeps_hls(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """`features.webrtc` of a protocol 3 server is not trusted; no WebRTC without live:read."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, protocol=3)
    assert camera_entity(hass, 12).camera_capabilities.frontend_stream_types == {StreamType.HLS}
    assert hub.webrtc_wanted(12) is False
    assert hub.ice_servers(99) == []


async def test_catalog_switches_webrtc_on_and_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The server switches WebRTC of a camera off: the entity is built anew as HLS."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server)
    assert hub.webrtc_built[12] is True
    hub.cameras[12]["features"]["webrtc"] = False
    hub._check_camera_kind(12)
    await hass.async_block_till_done()
    assert hub.webrtc_built[12] is False
    assert camera_entity(hass, 12).camera_capabilities.frontend_stream_types == {StreamType.HLS}
    # A camera that is gone is not built again.
    async_dispatcher_send(hass, hub.signal_camera_kind, 99)
    await hass.async_block_till_done()
    assert entity(hass, "camera", "42_99_camera") is None


async def test_protocol_4_reloads_a_protocol_3_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The server moves to protocol 4: the entry is set up anew (WebRTC, archive)."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, protocol=3)
    assert not hub.v4_enabled
    with pytest.MonkeyPatch.context() as patch:
        reloads: list[str] = []
        patch.setattr(
            hass.config_entries, "async_schedule_reload", lambda entry_id: reloads.append(entry_id)
        )
        hub.bridge.apply_reply({"config": v4_config()})
        assert reloads == [config_entry.entry_id]


# ------------------------------------------------------------- ask the archive


def ask_reply(
    items: list[dict[str, Any]],
    *,
    understood: bool = True,
    unsupported: list[dict[str, Any]] | None = None,
    has_more: bool = False,
) -> dict[str, Any]:
    """`V2SmartHomeHaAnswerDTO`."""
    return {
        "data": {
            "question": "коли сьогодні хтось підходив до хвіртки?",
            "from": "2026-10-08T00:00:00+03:00",
            "to": "2026-10-08T23:59:59+03:00",
            "camera_id": 12,
            "subjects": ["person"],
            "type": None,
            "fully_understood": understood,
            "unsupported": unsupported or [],
            "page": 1,
            "has_more": has_more,
            "items": items,
        }
    }


def ask_item(event_id: int, at: str, verdict: str | None = "person", *, clip: bool = True) -> dict[str, Any]:
    """One event of the answer."""
    return {
        "id": event_id,
        "camera_id": 12,
        "camera_name": "Gate",
        "occurred_at": at,
        "type": "motion",
        "verdict": verdict,
        "verdict_confidence": 0.91,
        "test": False,
        "has_frame": True,
        "links": {
            "snapshot": f"/v2/smart-home/ha/events/{event_id}/snapshot",
            "clip": f"/v2/smart-home/ha/events/{event_id}/clip" if clip else None,
        },
    }


async def ask(hass: HomeAssistant, question: str = "хто був біля хвіртки?", **extra: Any) -> dict[str, Any]:
    """Call the action and return its response."""
    return await hass.services.async_call(
        DOMAIN,
        "ask_archive",
        {"question": question, **extra},
        blocking=True,
        return_response=True,
    )


async def test_ask_archive_answers_with_a_phrase_and_events(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The question goes in the body; the answer names the count, the last time, the camera."""
    now = dt_util.now().replace(microsecond=0)
    earlier = (now - timedelta(minutes=30)).isoformat()
    later = (now - timedelta(minutes=5)).isoformat()
    replies = Replies((200, ask_reply([ask_item(5601, later), ask_item(5600, earlier, "vehicle", clip=False)])))
    await setup_v4(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        before=lambda mock: mock.post(f"{BASE}/ask", side_effect=replies),
    )

    result = await ask(hass, "  хто був біля хвіртки?  ", limit=5)
    assert replies.bodies == [{"question": "хто був біля хвіртки?", "page": 1, "limit": 5}]
    clock = dt_util.as_local(dt_util.parse_datetime(later)).strftime("%H:%M")
    assert result["answer"] == f"Found 2 events, the last one at {clock}: Gate, person."
    assert result["fully_understood"] is True
    assert result["has_more"] is False
    first, second = result["events"]
    assert first["id"] == 5601
    assert first["camera_id"] == 12
    assert first["camera"] == "Gate"
    assert first["entity_id"] == entity(hass, "camera", "42_12_camera")
    assert first["snapshot_url"] == f"/api/prostocam/{config_entry.entry_id}/events/5601/snapshot.jpg"
    assert first["clip"] == f"media-source://prostocam/{config_entry.entry_id}/12/event/5601"
    assert first["kind"] == "person"
    assert second["clip"] is None
    assert second["kind"] == "vehicle"

    # The phrase speaks the language of Home Assistant.
    hass.config.language = "uk"
    result = await ask(hass)
    assert result["answer"] == f"Знайшов 2 події, остання о {clock} — Gate, людина."


@pytest.mark.parametrize(
    ("reply", "language", "answer"),
    [
        (ask_reply([]), "en", "Nothing found."),
        (
            ask_reply([], understood=False, unsupported=[{"code": "zone", "text": "біля паркану"}]),
            "uk",
            "Не зрозумів частину питання: біля паркану.",
        ),
        (
            ask_reply([], understood=False, unsupported=[{"code": "negation"}]),
            "ru",
            "Не понял часть вопроса: negation.",
        ),
        (ask_reply([], understood=False), "bg", "Нищо не намерих. Не разбрах част от въпроса: ?."),
        (
            ask_reply([ask_item(1, "2020-01-02T03:04:00+00:00", None)], has_more=True),
            "en",
            "Found at least 1 event, the last one at {old}: Gate, motion.",
        ),
        (
            ask_reply([{**ask_item(2, "garbage"), "test": True}]),
            "uk",
            "Знайшов 1 подію.",
        ),
    ],
)
async def test_ask_archive_phrases(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    reply: dict[str, Any],
    language: str,
    answer: str,
) -> None:
    """Nothing found, a question not understood, more than a page, an old event."""
    await setup_v4(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        before=lambda mock: mock.post(f"{BASE}/ask", json=reply),
    )
    hass.config.language = language
    old = dt_util.as_local(dt_util.parse_datetime("2020-01-02T03:04:00+00:00")).strftime("%d.%m %H:%M")
    result = await ask(hass)
    assert result["answer"] == answer.format(old=old)


def test_events_noun_forms() -> None:
    """1 подію, 3 події, 5 подій, 11 подій, 21 подію, 22 події; one event, 2 events."""
    assert [events_noun("uk", n) for n in (1, 3, 5, 11, 21, 22, 112)] == [
        "подію",
        "події",
        "подій",
        "подій",
        "подію",
        "події",
        "подій",
    ]
    assert events_noun("en", 1) == "event"
    assert events_noun("bg", 2) == "събития"
    assert events_noun("de", 2) == "events"


async def test_ask_archive_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A lost area is remembered; the phrase of the server reaches the person."""
    replies = Replies((403, {"code": "scope_missing", "message": "archive:read"}))
    hub = await setup_v4(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        before=lambda mock: mock.post(f"{BASE}/ask", side_effect=replies),
    )
    with pytest.raises(HomeAssistantError) as err:
        await ask(hass)
    assert err.value.translation_key == "scope_missing"
    assert "archive:read" in hub.denied
    with pytest.raises(ServiceValidationError) as err:
        await ask(hass)
    assert err.value.translation_key == "no_access"

    hub.denied.clear()
    replies.replies = [(422, {"code": "invalid_body", "message": "Питання задовге"})]
    with pytest.raises(HomeAssistantError) as err:
        await ask(hass)
    assert err.value.translation_key == "refused"
    assert hub.archive.stats["question_errors"] == 2

    replies.replies = [(401, {"code": "token_invalid", "message": "x"})]
    with pytest.raises(HomeAssistantError) as err:
        await ask(hass)
    assert err.value.translation_key == "token_invalid"

    with pytest.raises(ServiceValidationError) as err:
        await ask(hass, config_entry_id="nope")
    assert err.value.translation_key == "entry_not_loaded"


async def test_ask_archive_needs_protocol_4_and_one_connection(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A protocol 3 server can not answer; without a loaded connection there is nobody to ask."""
    await setup_v4(hass, aioclient_mock, config_entry, mock_server, protocol=3)
    with pytest.raises(ServiceValidationError) as err:
        await ask(hass, config_entry_id=config_entry.entry_id)
    assert err.value.translation_key == "needs_protocol_4"

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await ask(hass)
    assert err.value.translation_key == "choose_entry"


async def test_assist_intent_asks_the_archive(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """«Знайди в архіві …»: the same search, the phrase is the speech of Assist."""
    replies = Replies((200, ask_reply([])))
    await setup_v4(
        hass,
        aioclient_mock,
        config_entry,
        mock_server,
        before=lambda mock: mock.post(f"{BASE}/ask", side_effect=replies),
    )
    response = await intent.async_handle(
        hass, "test", INTENT_ASK_ARCHIVE, {"question": {"value": "who came?"}}
    )
    assert response.speech["plain"]["speech"] == "Nothing found."
    assert replies.bodies[0]["question"] == "who came?"
    assert AskArchiveIntentHandler.description

    replies.replies = [(500, {"code": "boom"})]
    with pytest.raises(intent.IntentHandleError):
        await intent.async_handle(
            hass, "test", INTENT_ASK_ARCHIVE, {"question": {"value": "who came?"}}
        )

    # No connection may read the archive.
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(intent.IntentHandleError):
        await intent.async_handle(
            hass, "test", INTENT_ASK_ARCHIVE, {"question": {"value": "who came?"}}
        )


# --------------------------------------------------------------- export a clip


def job(state: str = "queued", **extra: Any) -> dict[str, Any]:
    """`V2SmartHomeHaExportDTO`."""
    ready = state == "ready"
    return {
        "data": {
            "job_id": 942,
            "camera_id": 12,
            "state": state,
            "pending": state in ("queued", "running"),
            "progress_percent": 100 if ready else 0,
            "from": "2026-10-08T14:05:00+03:00",
            "to": "2026-10-08T14:06:30+03:00",
            "error_code": None,
            "content_type": "video/mp4" if ready else None,
            "size_bytes": 1234567 if ready else None,
            "sha256": "ab" * 32 if ready else None,
            "download_url": DOWNLOAD if ready else None,
            "download_expires_at": "2026-10-08T15:10:00+03:00" if ready else None,
            "status_href": "/v2/smart-home/ha/cameras/12/exports/942",
            **extra,
        }
    }


async def setup_export(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    order: Replies,
    status: Replies,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """An entry whose server takes exports; reads of a job do not wait 5 s."""
    monkeypatch.setattr(const, "EXPORT_POLL", 0)

    def _before(mock: AiohttpClientMocker) -> None:
        mock.post(f"{BASE}/cameras/12/exports", side_effect=order)
        mock.get(f"{BASE}/cameras/12/exports/942", side_effect=status)

    return await setup_v4(hass, aioclient_mock, config_entry, mock_server, before=_before)


async def export(hass: HomeAssistant, start: str, **extra: Any) -> dict[str, Any] | None:
    """Call the action with the gate camera."""
    return await hass.services.async_call(
        DOMAIN,
        "export_clip",
        {
            "camera": entity(hass, "camera", "42_12_camera"),
            "start": start,
            "duration": 90,
            **extra,
        },
        blocking=True,
        return_response=True,
    )


async def test_export_clip_waits_for_the_file(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order → read until ready → the full link, SHA-256, the event, a Media item."""
    order = Replies((202, job("queued")))
    status = Replies((200, job("running")), aiohttp.ClientError("lost"), (200, job("ready")))
    hub = await setup_export(hass, aioclient_mock, config_entry, mock_server, order, status, monkeypatch)
    events = async_capture_events(hass, EVENT_EXPORT_READY)

    start = (dt_util.now() - timedelta(minutes=10)).replace(microsecond=0)
    result = await export(hass, start.isoformat())
    assert order.bodies == [{"from": start.isoformat(), "duration_s": 90}]
    assert result["state"] == "ready"
    assert result["url"] == f"https://new.prosto.cam{DOWNLOAD}"
    assert result["sha256"] == "ab" * 32
    assert result["size_bytes"] == 1234567
    assert result["camera"] == "Gate"
    assert result["media_content_id"] == f"media-source://prostocam/{config_entry.entry_id}/12/export/942"
    assert len(status.bodies) == 3
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["url"] == result["url"]

    # Media lists the export and takes a fresh link for every playback.
    source = ProstoCamMediaSource(hass)
    entry_id = config_entry.entry_id
    camera = await source.async_browse_media(MediaSourceItem(hass, DOMAIN, f"{entry_id}/12", None))
    assert camera.children[-1].identifier == f"{entry_id}/12/exports"
    folder = await source.async_browse_media(
        MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/exports", None)
    )
    assert folder.children[0].identifier == f"{entry_id}/12/export/942"
    assert "90 s" in folder.children[0].title
    played = await source.async_resolve_media(
        MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/export/942", None)
    )
    assert played.url == f"https://new.prosto.cam{DOWNLOAD}"
    assert played.mime_type == "video/mp4"

    status.replies = [(200, job("running"))]
    with pytest.raises(Unresolvable, match="still being prepared"):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/export/942", None)
        )
    status.replies = [(200, job("failed", error_code="no_recording"))]
    with pytest.raises(Unresolvable, match="could not prepare"):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/export/942", None)
        )
    folder = await source.async_browse_media(
        MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/exports", None)
    )
    assert folder.children[0].can_play is False
    status.replies = [(404, {"code": "export_not_found", "message": "Завдання немає"})]
    with pytest.raises(Unresolvable, match="Завдання немає"):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/export/942", None)
        )
    status.replies = [(500, {})]
    with pytest.raises(Unresolvable, match="HTTP 500"):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{entry_id}/12/export/942", None)
        )

    # Diagnostics: the job, never its link.
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert "one-time-secret" not in str(diagnostics)
    assert diagnostics["cameras"]["archive"]["exports"]["12"][0]["job_id"] == 942
    assert hub.archive.stats["exports_ready"] == 1


async def test_export_clip_without_waiting_and_a_naive_time(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wait: false` returns the job at once; a time without a zone is a time of HA."""
    order = Replies((202, job("queued")))
    status = Replies((200, job("ready")))
    await setup_export(hass, aioclient_mock, config_entry, mock_server, order, status, monkeypatch)
    start = (dt_util.now() - timedelta(hours=1)).replace(microsecond=0)
    result = await export(hass, start.replace(tzinfo=None).isoformat(), wait=False)
    assert result["pending"] is True
    assert result["url"] is None
    assert status.bodies == []
    assert order.bodies[0]["from"] == start.isoformat()


async def test_export_clip_gives_up_after_five_minutes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job still running after the wait: its number and state, no link."""
    order = Replies((202, job("queued")))
    status = Replies((200, job("running")))
    await setup_export(hass, aioclient_mock, config_entry, mock_server, order, status, monkeypatch)
    monkeypatch.setattr(const, "EXPORT_WAIT", 3)
    result = await export(hass, (dt_util.now() - timedelta(hours=1)).isoformat())
    assert result["job_id"] == 942
    assert result["state"] == "running"
    assert result["pending"] is True
    assert len(status.bodies) == 3


async def test_export_clip_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed job, lost job, refused order, a time in the future, not a camera."""
    order = Replies((202, job("queued")))
    status = Replies((200, job("failed", error_code="no_recording")))
    hub = await setup_export(hass, aioclient_mock, config_entry, mock_server, order, status, monkeypatch)
    past = (dt_util.now() - timedelta(hours=1)).isoformat()

    with pytest.raises(HomeAssistantError) as err:
        await export(hass, past)
    assert err.value.translation_key == "export_failed"
    assert err.value.translation_placeholders == {"error": "no_recording"}

    status.replies = [(404, {"code": "export_not_found", "message": "Завдання немає"})]
    with pytest.raises(HomeAssistantError) as err:
        await export(hass, past)
    assert err.value.translation_key == "refused"

    status.replies = [(401, {"code": "token_invalid", "message": "x"})]
    with pytest.raises(HomeAssistantError) as err:
        await export(hass, past)
    assert err.value.translation_key == "token_invalid"

    hub.bridge.auth_failed = False
    order.replies = [(422, {"code": "invalid_body", "message": "Відрізок у майбутньому"})]
    with pytest.raises(HomeAssistantError) as err:
        await export(hass, past)
    assert err.value.translation_placeholders == {"message": "Відрізок у майбутньому"}

    order.replies = [(202, {"data": {"state": "queued"}})]
    with pytest.raises(HomeAssistantError) as err:
        await export(hass, past)
    assert err.value.translation_key == "export_failed"

    with pytest.raises(ServiceValidationError) as err:
        await export(hass, (dt_util.now() + timedelta(minutes=1)).isoformat())
    assert err.value.translation_key == "export_in_future"

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "export_clip",
            {
                "camera": entity(hass, "binary_sensor", "42_12_connectivity"),
                "start": past,
                "duration": 90,
            },
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "not_a_camera_entity"


async def test_export_link_forms(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A full link stays; a path gets the server; anything else is no link."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server)
    archive = hub.archive
    assert archive.download_url({"download_url": "https://cdn.example.org/f.mp4"}) == "https://cdn.example.org/f.mp4"
    assert archive.download_url({"download_url": "/v1/x"}) == "https://new.prosto.cam/v1/x"
    assert archive.download_url({"download_url": "ftp://x"}) is None
    assert archive.download_url({}) is None


async def test_exports_survive_a_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
    hass_storage: dict[str, Any],
) -> None:
    """The list of exports (never a link) is kept in the storage of Home Assistant."""
    hass_storage[f"prostocam.{config_entry.entry_id}.exports"] = {
        "version": 1,
        "key": f"prostocam.{config_entry.entry_id}.exports",
        "data": {
            "exports": {
                "12": [
                    {"job_id": 7, "from": "2026-10-08T10:00:00+03:00", "duration_s": 30, "state": "ready"},
                    {"job_id": 8, "state": "failed"},
                    "bad",
                ],
                "x": [],
                "14": "bad",
            }
        },
    }
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server)
    assert [item["job_id"] for item in hub.archive.exports[12]] == [7, 8]
    source = ProstoCamMediaSource(hass)
    folder = await source.async_browse_media(
        MediaSourceItem(hass, DOMAIN, f"{config_entry.entry_id}/12/exports", None)
    )
    assert [child.can_play for child in folder.children] == [True, False]
    assert folder.children[1].title.startswith("#8")
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    saved = hass_storage[f"prostocam.{config_entry.entry_id}.exports"]["data"]
    assert saved["exports"]["12"][0]["job_id"] == 7


async def test_media_exports_need_protocol_4(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """A protocol 3 server: no exports in Media even with a stored list."""
    hub = await setup_v4(hass, aioclient_mock, config_entry, mock_server, protocol=3)
    hub.archive.exports[12] = [{"job_id": 1}]
    source = ProstoCamMediaSource(hass)
    camera = await source.async_browse_media(
        MediaSourceItem(hass, DOMAIN, f"{config_entry.entry_id}/12", None)
    )
    assert not any(child.identifier.endswith("/exports") for child in camera.children)
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, f"{config_entry.entry_id}/12/export/1", None)
        )
