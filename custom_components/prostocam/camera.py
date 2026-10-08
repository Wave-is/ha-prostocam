"""Cameras of ProstoCAM: live video over WebRTC or HLS (`stream`), snapshots.

Home Assistant offers a camera with its own WebRTC nothing but WebRTC (no HLS
to fall back to), so there are two classes (contract §14.2): a camera the
server marks `features.webrtc` talks WebRTC with the media node directly
(the platform only passes the offer and the answer), any other camera plays
HLS through the `stream` component. A camera whose WebRTC fails is built anew
as HLS; the catalog (every 5 minutes) may bring it back.
"""

from __future__ import annotations

import time
from typing import Any

from webrtc_models import RTCConfiguration, RTCIceCandidateInit, RTCIceServer

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    WebRTCAnswer,
    WebRTCClientConfiguration,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.components.stream import Stream
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProstoCamConfigEntry
from .cameras import ProstoCamCameras, WebRtcFailed, async_add_camera_entities
from .const import (
    DEFAULT_TITLE,
    LIVE_URL_MAX_AGE,
    LOGGER,
    SCOPE_CAMERAS,
    SCOPE_LIVE,
    STREAM_REFRESH_MIN,
)
from .entity import ProstoCamCameraEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a camera entity for every camera Home Assistant may see."""
    hub = entry.runtime_data.cameras
    if hub is None or not hub.has(SCOPE_CAMERAS):
        return
    entities: dict[int, ProstoCamCamera] = {}

    def _build(camera_id: int) -> list[ProstoCamCamera]:
        kind = ProstoCamWebRtcCamera if hub.webrtc_wanted(camera_id) else ProstoCamCamera
        entity = kind(hub, camera_id)
        entities[camera_id] = entity
        return [entity]

    async def _rebuild(camera_id: int) -> None:
        old = entities.pop(camera_id, None)
        if old is not None:
            await old.async_remove()
        if camera_id in hub.cameras:
            async_add_entities(_build(camera_id))

    @callback
    def _kind_changed(camera_id: int) -> None:
        # Not inside the offer that failed: the entity is removed after it.
        entry.async_create_task(hass, _rebuild(camera_id), "prostocam_camera_kind")

    async_add_camera_entities(hub, entry, _build, async_add_entities)
    entry.async_on_unload(
        async_dispatcher_connect(hass, hub.signal_camera_kind, _kind_changed)
    )


class ProstoCamCamera(ProstoCamCameraEntity, Camera):
    """One ProstoCAM camera.

    Every start of a stream asks the server for a fresh live address (its token
    lives 5 minutes; a started session lives on by the cookie of the media node).
    WebRTC providers (go2rtc) are not offered: go2rtc re-reads the HLS playlist
    without cookies and loses the stream after 5 minutes (contract §10.3).
    """

    _attr_name = None
    _webrtc = False

    def __init__(self, hub: ProstoCamCameras, camera_id: int) -> None:
        """Create the camera entity."""
        Camera.__init__(self)
        self._init_camera(hub, camera_id, "camera")
        item = hub.cameras.get(camera_id, {})
        features = item.get("features")
        live_feature = features.get("live") if isinstance(features, dict) else None
        if hub.has(SCOPE_LIVE) and live_feature is not False:
            self._attr_supported_features = CameraEntityFeature.STREAM
        else:
            self._attr_supported_features = CameraEntityFeature(0)
        self._attr_brand = item.get("manufacturer") or DEFAULT_TITLE
        self._attr_model = item.get("model")
        self._source_at: float | None = None
        self._refresh_at: float | None = None
        hub.webrtc_built[camera_id] = self._webrtc

    @property
    def is_streaming(self) -> bool:
        """The camera publishes its video now."""
        return bool(self.camera_state.streaming)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Place and security zone of the camera in ProstoCAM."""
        item = self.hub.cameras.get(self.camera_id, {})
        features = item.get("features")
        return {
            "location": item.get("location"),
            "zone": item.get("zone"),
            "ai_mode": features.get("ai_mode") if isinstance(features, dict) else None,
            "camera_id": self.camera_id,
            "webrtc": self._webrtc,
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """The frame "now" (cached 10 s)."""
        return await self.hub.async_snapshot(self.camera_id)

    async def stream_source(self) -> str | None:
        """A fresh live address for this start; never cached."""
        url = await self.hub.async_live_url(self.camera_id)
        if url is not None:
            self._source_at = time.monotonic()
        return url

    async def async_refresh_providers(self, *args: Any, **kwargs: Any) -> None:
        """Offer HLS only: no WebRTC provider, and no live start just to probe one."""
        return

    async def async_create_stream(self) -> Stream | None:
        """Reuse the stream, but give a stopped or broken one a fresh address."""
        existing = self.stream
        stream = await super().async_create_stream()
        if stream is None:
            return None
        stream.set_update_callback(self._async_stream_updated)
        if existing is None:
            return stream  # just created from a fresh address
        thread = getattr(stream, "_thread", None)
        running = thread is not None and thread.is_alive()
        if running and stream.available:
            return stream
        if (
            self._source_at is not None
            and time.monotonic() - self._source_at < LIVE_URL_MAX_AGE
        ):
            return stream
        await self._async_refresh_source(stream)
        return stream

    @callback
    def _async_stream_updated(self) -> None:
        """The stream worker failed: its address may have expired."""
        self.async_write_ha_state()
        stream = self.stream
        if stream is None or stream.available:
            return
        now = time.monotonic()
        if self._refresh_at is not None and now - self._refresh_at < STREAM_REFRESH_MIN:
            return
        self._refresh_at = now
        self.hass.async_create_task(self._async_refresh_source(stream))

    async def _async_refresh_source(self, stream: Stream) -> None:
        url = await self.stream_source()
        if url is None or stream is not self.stream:
            return
        LOGGER.debug("ProstoCAM camera %s: fresh live address", self.camera_id)
        stream.update_source(url)


class ProstoCamWebRtcCamera(ProstoCamCamera):
    """A ProstoCAM camera that talks WebRTC with the media node (below a second).

    The offer of the browser goes to the server as it is and the answer of the
    media node comes back as it is; the video goes from the media node to the
    browser directly. The node is ICE-lite with a public address: candidates of
    the browser are not needed, the session ends by itself when the card closes.
    `stream_source()` stays for recordings and stills.
    """

    _webrtc = True

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Pass the offer to ProstoCAM and the answer (or the refusal) to the browser."""
        try:
            answer = await self.hub.async_webrtc_answer(self.camera_id, offer_sdp)
        except WebRtcFailed as err:
            send_message(WebRTCError(err.code, err.message))
            return
        send_message(WebRTCAnswer(answer=answer))

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Candidates of the browser are not needed by the ICE-lite media node."""
        return

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """The media node ends the session itself (15 s without ICE)."""
        return

    @callback
    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """ICE servers named by ProstoCAM, if any (Home Assistant adds its own)."""
        servers = [
            RTCIceServer(
                urls=server["urls"],
                username=server.get("username"),
                credential=server.get("credential"),
            )
            for server in self.hub.ice_servers(self.camera_id)
        ]
        return WebRTCClientConfiguration(
            configuration=RTCConfiguration(ice_servers=servers)
        )
