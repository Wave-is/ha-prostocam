"""Cameras of ProstoCAM in Home Assistant (protocol 2, contract §8–§9).

What it does:

* reads the catalog of the cameras the subscriber let Home Assistant see
  (`GET /v2/smart-home/ha/cameras`) at start and every 5 minutes; a camera that
  appears gets its device and entities at once, a camera that is gone loses its
  device;
* opens a fresh live address for every start of a stream (the start token lives
  5 minutes; the media node keeps a started session by its cookie);
* reads the snapshot "now" with a 10 s cache;
* keeps one live event channel (SSE `/v2/stream`) per config entry: resumes
  with `Last-Event-ID` (stored), reconnects with a pause of 3 s … 5 min, and
  turns `alarm` into motion/person/vehicle sensors, the alarm event and the
  last alarm frame, `camera.status` into the connectivity sensor;
* creates nothing the token is not allowed to see, and raises a repair issue
  that says where to give the access.

Tokens, live addresses and the live key never reach the log or diagnostics.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import (
    ProstoCamAuthError,
    ProstoCamClient,
    ProstoCamError,
    ProstoCamRateLimitedError,
    ProstoCamScopeError,
    ProstoCamUnavailableError,
)
from .const import (
    CAMERA_SCOPES,
    CAMERAS_PROTOCOL,
    CATALOG_INTERVAL,
    CONTROL_PROTOCOL,
    CONTROL_SCOPES,
    DEFAULT_TITLE,
    DETECTION_RESET,
    DETECTIONS,
    DOMAIN,
    ERROR_FRAME_MISSING,
    EVENT_ALARM,
    EVENT_TYPE_OF_CLASS,
    FRAME_CACHE_SIZE,
    FRAME_RETRY,
    ISSUE_MISSING_ACCESS,
    LOGGER,
    PATH_CAMERAS,
    RATE_LIMIT_PAUSE,
    SCOPE_ACCOUNT,
    SCOPE_ACTIONS,
    SCOPE_ARCHIVE,
    SCOPE_ARMING,
    SCOPE_CAMERAS,
    SCOPE_EVENTS,
    SCOPE_LIVE,
    SNAPSHOT_CACHE,
    SSE_RETRY_MAX,
    SSE_RETRY_MIN,
    SSE_SAVE_DELAY,
    STORAGE_VERSION,
    VIEW_EVENT_SNAPSHOT,
)
from .sse import SseEvent, SseParser
from .words import alarm_label

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .bridge import ProstoCamBridge
    from .control import ProstoCamControl

# Reasons of `session.closed` after which the channel is opened again at once.
RECONNECT_AT_ONCE = frozenset({"token_expired", "server_restart"})


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@dataclass
class CameraState:
    """What Home Assistant knows about one camera between catalog reads."""

    online: bool | None = None
    streaming: bool | None = None
    video_state: str | None = None
    last_frame_at: str | None = None
    detections: dict[str, bool] = field(
        default_factory=lambda: dict.fromkeys(DETECTIONS, False)
    )
    reset_unsubs: dict[str, CALLBACK_TYPE] = field(default_factory=dict)
    last_alarm: dict[str, Any] | None = None
    incident: dict[str, Any] | None = None
    problems: set[str] = field(default_factory=set)
    alarm_image: bytes | None = None
    alarm_image_type: str = "image/jpeg"
    alarm_image_at: datetime | None = None
    # Protocol 3: whose frame the alarm image is (`event` or `now`) and of which event.
    alarm_image_source: str | None = None
    alarm_event_id: int | None = None
    # The last alarm event of the camera (for "Check with AI" and "Deter").
    last_event_id: int | None = None
    # Mute of the alarms of the camera (`actions:write`).
    muted: bool | None = None
    muted_until: str | None = None
    mute_choice: str | None = None
    # Deterrence outputs of the device; None = not asked yet.
    deter_capable: bool | None = None
    deter_actions: list[str] = field(default_factory=list)
    snapshot: bytes | None = None
    snapshot_type: str = "image/jpeg"
    snapshot_mono: float | None = None


class ProstoCamCameras:
    """Catalog, live addresses, snapshots and the event channel of one entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ProstoCamClient,
        bridge: ProstoCamBridge,
    ) -> None:
        """Prepare; nothing runs until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self.bridge = bridge
        self.uid = str(entry.unique_id or entry.entry_id)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.events"
        )
        # What the server allowed when this entry was set up; a change reloads it.
        self.protocol: int | None = None
        self.capabilities: frozenset[str] | None = None
        # Areas the server refused although `capabilities` listed them.
        self.denied: set[str] = set()
        self.cameras: dict[int, dict[str, Any]] = {}
        self.states: dict[int, CameraState] = {}
        self.catalog_loaded = False
        self.platforms_loaded = False
        self.cursor: str | None = None
        self.stats: dict[str, Any] = {
            "catalog_reads": 0,
            "catalog_errors": 0,
            "last_catalog_at": None,
            "live_starts": 0,
            "live_errors": 0,
            "snapshots": 0,
            "snapshot_errors": 0,
            "events": {},
            "unknown_camera_events": 0,
            "stream_connects": 0,
            "stream_connected": False,
            "stream_connected_at": None,
            "stream_last_close": None,
            "stream_last_error": None,
            "stream_resumed": None,
        }
        self._clock = time.monotonic
        self._stopped = False
        self._reload_scheduled = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._events_task: asyncio.Task[None] | None = None
        self._image_tasks: set[asyncio.Task[None]] = set()
        self._backoff = 0.0
        # Protocol 3: arming, buttons and money (set up right after this hub).
        self.control: ProstoCamControl | None = None
        # Frames of events do not change: a few stay in memory for the media browser.
        self._frames: OrderedDict[int, tuple[bytes, str]] = OrderedDict()

    # ------------------------------------------------------------- signals

    @property
    def signal_new(self) -> str:
        """Dispatcher signal: new camera ids appeared in the catalog."""
        return f"{DOMAIN}_{self.entry.entry_id}_new"

    def signal_update(self, camera_id: int) -> str:
        """Dispatcher signal: the state of one camera changed."""
        return f"{DOMAIN}_{self.entry.entry_id}_update_{camera_id}"

    def signal_alarm(self, camera_id: int) -> str:
        """Dispatcher signal: an alarm of one camera (event type, attributes)."""
        return f"{DOMAIN}_{self.entry.entry_id}_alarm_{camera_id}"

    # -------------------------------------------------------------- access

    @property
    def enabled(self) -> bool:
        """The server speaks protocol 2 and told us the areas of the token."""
        return (
            self.protocol is not None
            and self.protocol >= CAMERAS_PROTOCOL
            and self.capabilities is not None
        )

    def has(self, scope: str) -> bool:
        """Whether the token may use an area now."""
        return (
            self.enabled
            and self.capabilities is not None
            and scope in self.capabilities
            and scope not in self.denied
        )

    @property
    def control_enabled(self) -> bool:
        """The server speaks protocol 3: media, arming, buttons and money."""
        return self.enabled and self.protocol is not None and self.protocol >= CONTROL_PROTOCOL

    def has_control(self, scope: str) -> bool:
        """Whether the token may use an area of protocol 3 now."""
        return self.control_enabled and self.has(scope)

    @property
    def missing_scopes(self) -> list[str]:
        """Areas the subscriber did not give Home Assistant (of the server's protocol)."""
        if not self.enabled:
            return []
        scopes = list(CAMERA_SCOPES)
        if self.control_enabled:
            scopes += CONTROL_SCOPES
        return [scope for scope in scopes if not self.has(scope)]

    @property
    def platforms_needed(self) -> bool:
        """Entities exist only when the catalog, the arming or the account may be read."""
        return (
            self.has(SCOPE_CAMERAS)
            or self.has_control(SCOPE_ARMING)
            or self.has_control(SCOPE_ACCOUNT)
        )

    @property
    def camera_actions(self) -> bool:
        """Buttons and mute of the cameras (`actions:write` and the catalog)."""
        return self.has(SCOPE_CAMERAS) and self.has_control(SCOPE_ACTIONS)

    def camera_name(self, camera_id: int) -> str:
        """The name of a camera as the subscriber called it."""
        item = self.cameras.get(camera_id, {})
        return _str(item.get("name")) or f"{DEFAULT_TITLE} {camera_id}"

    def snapshot_path(self, event_id: int) -> str:
        """Path of the frame of an event through Home Assistant (needs a login or a signature)."""
        return VIEW_EVENT_SNAPSHOT.format(entry_id=self.entry.entry_id, event_id=event_id)

    # ----------------------------------------------------------- life cycle

    async def async_start(self) -> None:
        """Take the access of the token from the bridge and start reading."""
        self.protocol = self.bridge.server_protocol
        self.capabilities = self.bridge.capabilities
        stored = await self._store.async_load() or {}
        cursor = stored.get("cursor")
        self.cursor = cursor if isinstance(cursor, str) and cursor else None
        self._unsubs.append(self.bridge.async_add_config_listener(self._async_config_changed))
        if self.protocol is not None and self.protocol < CAMERAS_PROTOCOL:
            LOGGER.info(
                "This ProstoCAM server speaks protocol %s: cameras need protocol %s",
                self.protocol,
                CAMERAS_PROTOCOL,
            )
        self._update_issue()
        if not self.enabled:
            return
        if not self.has(SCOPE_CAMERAS):
            self._remove_devices(keep=set())
            return
        await self.async_refresh_catalog()
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_catalog_tick,
                timedelta(seconds=CATALOG_INTERVAL),
                cancel_on_shutdown=True,
            )
        )
        if self.has(SCOPE_EVENTS):
            self._events_task = self.entry.async_create_background_task(
                self.hass, self._async_run_events(), "prostocam_events"
            )

    async def async_stop(self) -> None:
        """Stop the channel and timers; keep the cursor for the next start."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for state in self.states.values():
            for unsub in state.reset_unsubs.values():
                unsub()
            state.reset_unsubs.clear()
        tasks = [task for task in (self._events_task, *self._image_tasks) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._events_task = None
        await self._store.async_save(self._data_to_save())

    @callback
    def _async_config_changed(self) -> None:
        """The server changed the protocol or the areas: build the entities anew."""
        if self._stopped or self._reload_scheduled:
            return
        protocol = self.bridge.server_protocol
        capabilities = self.bridge.capabilities
        if capabilities is None:
            return
        now_enabled = protocol is not None and protocol >= CAMERAS_PROTOCOL
        now_control = now_enabled and protocol is not None and protocol >= CONTROL_PROTOCOL
        if (
            now_enabled == self.enabled
            and now_control == self.control_enabled
            and (not now_enabled or capabilities == self.capabilities)
        ):
            return
        LOGGER.info("ProstoCAM changed what Home Assistant may see; reloading")
        self._reload_scheduled = True
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    def deny(self, scope: str, err: ProstoCamScopeError) -> None:
        """The server refused an area `capabilities` listed: no more requests of it."""
        self._deny(scope, err)

    def _deny(self, scope: str, err: ProstoCamScopeError) -> None:
        if scope not in self.denied:
            LOGGER.warning(
                "ProstoCAM refused %s (%s); give Home Assistant the access in the web account",
                scope,
                err.code,
            )
        self.denied.add(scope)
        self._update_issue()

    def _update_issue(self) -> None:
        missing = self.missing_scopes
        if not missing:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_MISSING_ACCESS)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_MISSING_ACCESS,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MISSING_ACCESS,
            translation_placeholders={"scopes": ", ".join(missing)},
        )

    # -------------------------------------------------------------- catalog

    async def _async_catalog_tick(self, _now: Any) -> None:
        await self.async_refresh_catalog()

    async def async_refresh_catalog(self) -> None:
        """Read the cameras Home Assistant may see; add and remove devices."""
        if self._stopped or not self.has(SCOPE_CAMERAS) or self.bridge.auth_failed:
            return
        try:
            data = await self.client.async_get(PATH_CAMERAS)
        except ProstoCamScopeError as err:
            self._deny(SCOPE_CAMERAS, err)
            return
        except ProstoCamAuthError:
            self.bridge.report_auth_failed()
            return
        except ProstoCamError as err:
            self.stats["catalog_errors"] += 1
            LOGGER.debug("ProstoCAM camera catalog not read: %s", err)
            return
        self.stats["catalog_reads"] += 1
        self.stats["last_catalog_at"] = dt_util.utcnow().isoformat()
        items = data.get("items")
        cameras: dict[int, dict[str, Any]] = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                camera_id = _int(item.get("id"))
                if camera_id is not None and camera_id > 0:
                    cameras[camera_id] = item
        new_ids = [camera_id for camera_id in cameras if camera_id not in self.cameras]
        self.cameras = cameras
        self.catalog_loaded = True
        for camera_id, item in cameras.items():
            state = self.states.setdefault(camera_id, CameraState())
            online = item.get("online")
            if isinstance(online, bool):
                state.online = online
            streaming = item.get("streaming")
            if isinstance(streaming, bool):
                state.streaming = streaming
            state.video_state = _str(item.get("video_state")) or state.video_state
            state.last_frame_at = _str(item.get("last_frame_at")) or state.last_frame_at
            self._update_device(camera_id)
            async_dispatcher_send(self.hass, self.signal_update(camera_id))
        self._remove_devices(keep=set(cameras))
        if new_ids and self.control is not None and self.camera_actions:
            # Which buttons a camera gets depends on its outputs: ask before adding it.
            await self.control.async_refresh_cameras(new_ids)
        if new_ids:
            async_dispatcher_send(self.hass, self.signal_new, new_ids)
        if isinstance(data.get("config"), dict):
            # The catalog carries the same `config` as every reply of the server.
            self.bridge.apply_reply({"config": data["config"]})

    def camera_for(self, raw_id: Any) -> int | None:
        """Our camera number for an id of an event (a card or a device number)."""
        camera_id = _int(raw_id)
        if camera_id is None:
            return None
        if camera_id in self.cameras:
            return camera_id
        for card, item in self.cameras.items():
            if _int(item.get("device_id")) == camera_id:
                return card
        return None

    def device_info(self, camera_id: int) -> DeviceInfo:
        """The Home Assistant device of one camera."""
        item = self.cameras.get(camera_id, {})
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_identifier(camera_id))},
            name=_str(item.get("name")) or f"{DEFAULT_TITLE} {camera_id}",
            manufacturer=_str(item.get("manufacturer")) or DEFAULT_TITLE,
            model=_str(item.get("model")),
            configuration_url=self.client.server,
        )

    def device_of(self, camera_id: int) -> dr.DeviceEntry | None:
        """The Home Assistant device of a camera of this entry (None before its entities exist)."""
        identifier = (DOMAIN, self.device_identifier(camera_id))
        return next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    dr.async_get(self.hass), self.entry.entry_id
                )
                if identifier in device.identifiers
            ),
            None,
        )

    def device_identifier(self, camera_id: int) -> str:
        """Identifier of the device of a camera, unique per connection."""
        return f"{self.uid}_camera_{camera_id}"

    def _update_device(self, camera_id: int) -> None:
        """Names and models change in the web account; follow them."""
        dev_reg = dr.async_get(self.hass)
        identifier = (DOMAIN, self.device_identifier(camera_id))
        device = next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    dev_reg, self.entry.entry_id
                )
                if identifier in device.identifiers
            ),
            None,
        )
        if device is None:
            return
        info = self.device_info(camera_id)
        changes: dict[str, Any] = {}
        for key in ("name", "manufacturer", "model"):
            if getattr(device, key) != info.get(key):
                changes[key] = info.get(key)
        if changes:
            dev_reg.async_update_device(device.id, **changes)

    def _remove_devices(self, keep: set[int]) -> None:
        """Devices of cameras Home Assistant may no longer see go away."""
        dev_reg = dr.async_get(self.hass)
        prefix = f"{self.uid}_camera_"
        for device in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            for domain, identifier in device.identifiers:
                if domain != DOMAIN or not identifier.startswith(prefix):
                    continue
                camera_id = _int(identifier[len(prefix) :])
                if camera_id is not None and camera_id not in keep:
                    LOGGER.info("ProstoCAM camera %s is no longer shared; removing it", camera_id)
                    # The device belongs to this connection only.
                    dev_reg.async_remove_device(device.id)
                    if (state := self.states.pop(camera_id, None)) is not None:
                        for unsub in state.reset_unsubs.values():
                            unsub()
                break

    def expected_unique_ids(self) -> set[str]:
        """Unique ids of every entity the current access allows."""
        keys: list[str] = []
        if self.has(SCOPE_CAMERAS):
            keys += ["camera", "connectivity"]
            if self.has(SCOPE_EVENTS):
                keys += [*DETECTIONS, "alarm", "last_alarm"]
        if self.camera_actions:
            keys += ["test_alarm", "ai_check", "do_not_disturb"]
        expected = {
            self.unique_id(camera_id, key) for camera_id in self.cameras for key in keys
        }
        if self.camera_actions:
            expected |= {
                self.unique_id(camera_id, "deter")
                for camera_id in self.cameras
                # Unknown (the server was not asked yet) keeps the button.
                if self.states.get(camera_id, CameraState()).deter_capable is not False
            }
        if self.control is not None:
            expected |= self.control.expected_unique_ids()
        return expected

    def unique_id(self, camera_id: int, key: str) -> str:
        """Unique id of one entity of a camera."""
        return f"{self.uid}_{camera_id}_{key}"

    @callback
    def async_remove_stale_entities(self) -> None:
        """Entities of areas or cameras Home Assistant lost access to go away."""
        if not self.enabled:
            return  # the server was not reached: do not guess
        if not self.catalog_loaded and self.has(SCOPE_CAMERAS):
            return  # the catalog was not read: do not guess
        expected = self.expected_unique_ids()
        ent_reg = er.async_get(self.hass)
        prefix = f"{self.uid}_"
        for entity in er.async_entries_for_config_entry(ent_reg, self.entry.entry_id):
            if entity.unique_id.startswith(prefix) and entity.unique_id not in expected:
                ent_reg.async_remove(entity.entity_id)

    # ------------------------------------------------------- live and stills

    async def async_live_url(self, camera_id: int) -> str | None:
        """A fresh live address for one start of the stream (never cached)."""
        if not self.has(SCOPE_LIVE) or self.bridge.auth_failed:
            return None
        try:
            data = await self.client.async_get(f"{PATH_CAMERAS}/{camera_id}/live")
        except ProstoCamScopeError as err:
            self._deny(SCOPE_LIVE, err)
            return None
        except ProstoCamAuthError:
            self.bridge.report_auth_failed()
            return None
        except ProstoCamError as err:
            # The message holds the status and the code only, never the address.
            self.stats["live_errors"] += 1
            self.stats["last_live_error"] = str(err)
            LOGGER.warning("ProstoCAM camera %s: no live video (%s)", camera_id, err)
            return None
        url = data.get("hls_url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            self.stats["live_errors"] += 1
            return None
        self.stats["live_starts"] += 1
        return url

    async def async_snapshot(self, camera_id: int, *, fresh: bool = False) -> bytes | None:
        """The frame "now" of a camera; cached for 10 s."""
        state = self.states.setdefault(camera_id, CameraState())
        now = self._clock()
        if (
            not fresh
            and state.snapshot is not None
            and state.snapshot_mono is not None
            and now - state.snapshot_mono < SNAPSHOT_CACHE
        ):
            return state.snapshot
        if not self.has(SCOPE_CAMERAS) or self.bridge.auth_failed:
            return state.snapshot
        try:
            content, content_type = await self.client.async_get_image(
                f"{PATH_CAMERAS}/{camera_id}/snapshot"
            )
        except ProstoCamScopeError as err:
            self._deny(SCOPE_CAMERAS, err)
            return state.snapshot
        except ProstoCamAuthError:
            self.bridge.report_auth_failed()
            return state.snapshot
        except ProstoCamError as err:
            self.stats["snapshot_errors"] += 1
            LOGGER.debug("ProstoCAM camera %s: no snapshot (%s)", camera_id, err)
            if isinstance(err, ProstoCamUnavailableError):
                # Do not ask again before the server's pause (still the old frame).
                state.snapshot_mono = now - SNAPSHOT_CACHE + min(err.retry_after or 10, 60)
            return state.snapshot
        if not content:
            return state.snapshot
        self.stats["snapshots"] += 1
        state.snapshot = content
        state.snapshot_type = content_type
        state.snapshot_mono = now
        return content

    # --------------------------------------------------------- event channel

    async def _async_run_events(self) -> None:
        """Keep the live event channel open while the entry lives."""
        while not self._stopped and self.has(SCOPE_EVENTS) and not self.bridge.auth_failed:
            delay = await self._async_events_once()
            if delay is None or self._stopped:
                return
            await asyncio.sleep(delay)

    async def _async_events_once(self) -> float | None:
        """One connection; the pause before the next one, None = do not reconnect."""
        try:
            response = await self.client.async_open_stream(self.cursor)
        except ProstoCamAuthError:
            self.stats["stream_last_error"] = "token_invalid"
            self.bridge.report_auth_failed()
            return None
        except ProstoCamScopeError as err:
            self.stats["stream_last_error"] = err.code
            self._deny(SCOPE_EVENTS, err)
            return None
        except ProstoCamRateLimitedError as err:
            self.stats["stream_last_error"] = str(err)
            return max(RATE_LIMIT_PAUSE, self._next_backoff())
        except ProstoCamUnavailableError as err:
            self.stats["stream_last_error"] = str(err)
            return max(float(err.retry_after or 0), self._next_backoff())
        except ProstoCamError as err:
            self.stats["stream_last_error"] = str(err)
            return self._next_backoff()

        self.stats["stream_connects"] += 1
        self.stats["stream_connected"] = True
        self.stats["stream_connected_at"] = dt_util.utcnow().isoformat()
        reason: str | None = None
        got_events = False
        try:
            parser = SseParser()
            async for raw in response.content:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                event = parser.feed(line)
                if event is None:
                    continue
                got_events = True
                if parser.last_event_id and parser.last_event_id != self.cursor:
                    self.cursor = parser.last_event_id
                    self._store.async_delay_save(self._data_to_save, SSE_SAVE_DELAY)
                closed = self.handle_event(event)
                if closed is not None:
                    reason = closed
                    break
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            # A silent channel (no pulse for 45 s) ends here too.
            self.stats["stream_last_error"] = type(err).__name__
        finally:
            response.release()
            self.stats["stream_connected"] = False
        self.stats["stream_last_close"] = reason or "disconnected"
        if got_events:
            self._backoff = 0.0
        if reason in RECONNECT_AT_ONCE:
            return 0.5
        if reason == "session_revoked":
            # Access changed or the token was revoked: the next answer tells which.
            return float(SSE_RETRY_MIN)
        return self._next_backoff()

    def _next_backoff(self) -> float:
        self._backoff = min(SSE_RETRY_MAX, max(SSE_RETRY_MIN, self._backoff * 2))
        return self._backoff

    @callback
    def handle_event(self, event: SseEvent) -> str | None:
        """Apply one event of the channel; returns the reason when it closes."""
        events: dict[str, int] = self.stats["events"]
        events[event.event] = events.get(event.event, 0) + 1
        try:
            data = json.loads(event.data) if event.data else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if event.event == "hello":
            self.stats["stream_resumed"] = data.get("resumed") is True
            cursor = _str(data.get("cursor"))
            if cursor and not self.cursor:
                self.cursor = cursor
        elif event.event == "session.closed":
            return _str(data.get("reason")) or "closed"
        elif event.event == "resync":
            cursor = _str(data.get("cursor"))
            if cursor:
                self.cursor = cursor
            self.entry.async_create_task(
                self.hass, self.async_refresh_catalog(), "prostocam_resync"
            )
        elif event.event == "alarm":
            self._handle_alarm(data.get("alarm"), data.get("links"))
        elif event.event == "arming.changed":
            if self.control is not None:
                self.control.handle_arming_changed(data.get("arming"))
        elif event.event == "camera.status":
            self._handle_status(data.get("camera"))
        elif event.event.startswith("incident."):
            self._handle_incident(event.event, data.get("incident"))
        elif event.event == "problem.opened":
            self._handle_problem(data.get("problem"), opened=True)
        elif event.event == "problem.resolved":
            self._handle_problem(
                data.get("problem") or {"camera_id": data.get("camera_id"), "kind": data.get("kind")},
                opened=False,
            )
        return None

    def _handle_alarm(self, alarm: Any, links: Any = None) -> None:
        if not isinstance(alarm, dict):
            return
        camera_id = self.camera_for(alarm.get("camera_id"))
        if camera_id is None:
            self.stats["unknown_camera_events"] += 1
            return
        state = self.states.setdefault(camera_id, CameraState())
        classification = _str(alarm.get("classification")) or "other"
        test = alarm.get("test") is True
        event_type = "test" if test else EVENT_TYPE_OF_CLASS.get(classification, "other")
        confidence = alarm.get("confidence")
        event_id = _int(alarm.get("event_id"))
        attributes: dict[str, Any] = {
            "classification": classification,
            "confidence": confidence if isinstance(confidence, (int, float)) else None,
            # «Людина · 87 %» in the language of Home Assistant; without the
            # percent when nobody measured it (a camera detection sends 0).
            "label": alarm_label(self.hass.config.language, event_type, confidence),
            "event_id": event_id,
            "alarm_id": _int(alarm.get("id")),
            "created_at": _str(alarm.get("created_at")),
            "test": test,
        }
        # Protocol 3: the frame of the event itself (links come only on our channel).
        event_frame = (
            self.control_enabled
            and event_id is not None
            and (not isinstance(links, dict) or links.get("snapshot") is not None)
        )
        if event_frame and event_id is not None:
            attributes["snapshot"] = self.snapshot_path(event_id)
        if self.has_control(SCOPE_ARCHIVE) and event_id is not None:
            attributes["media_content_id"] = (
                f"media-source://{DOMAIN}/{self.entry.entry_id}/{camera_id}/event/{event_id}"
            )
        if state.incident is not None:
            attributes.update(
                {f"incident_{key}": value for key, value in state.incident.items()}
            )
        state.last_alarm = attributes
        if event_id is not None:
            state.last_event_id = event_id
        async_dispatcher_send(self.hass, self.signal_alarm(camera_id), event_type, attributes)
        self._detect(camera_id, "motion")
        if classification in ("person", "vehicle"):
            self._detect(camera_id, classification)
        if self.has(SCOPE_CAMERAS):
            task = self.entry.async_create_task(
                self.hass,
                self._async_alarm_image(camera_id, event_id if event_frame else None),
                "prostocam_alarm_image",
            )
            self._image_tasks.add(task)
            task.add_done_callback(self._image_tasks.discard)
        if self.control is not None:
            self.control.note_alarm(test=test)
        # For automations and the notification blueprint: at once, the frame
        # is read by the phone through Home Assistant when it shows the message.
        device = self.device_of(camera_id)
        self.hass.bus.async_fire(
            EVENT_ALARM,
            {
                "entry_id": self.entry.entry_id,
                "camera_id": camera_id,
                "camera_name": self.camera_name(camera_id),
                "device_id": device.id if device is not None else None,
                "event_type": event_type,
                **attributes,
            },
        )
        async_dispatcher_send(self.hass, self.signal_update(camera_id))

    def _detect(self, camera_id: int, kind: str) -> None:
        state = self.states[camera_id]
        state.detections[kind] = True
        if (unsub := state.reset_unsubs.pop(kind, None)) is not None:
            unsub()

        @callback
        def _reset(_now: Any) -> None:
            state.reset_unsubs.pop(kind, None)
            state.detections[kind] = False
            async_dispatcher_send(self.hass, self.signal_update(camera_id))

        state.reset_unsubs[kind] = async_call_later(self.hass, DETECTION_RESET, _reset)

    async def _async_alarm_image(self, camera_id: int, event_id: int | None) -> None:
        """The frame of the alarm: the saved frame of the event (protocol 3).

        A server of protocol 2, or an event without a saved frame, gives the
        snapshot "now" instead (the attribute `frame` of the image says which).
        """
        frame: tuple[bytes, str] | None = None
        if event_id is not None:
            frame = await self.async_event_image(event_id)
            if frame is None and not self._stopped:
                # The frame may be still on its way to the archive.
                await asyncio.sleep(FRAME_RETRY)
                frame = await self.async_event_image(event_id)
        source = "event"
        if frame is None:
            content = await self.async_snapshot(camera_id, fresh=True)
            state = self.states.get(camera_id)
            if content is None or state is None:
                return
            frame = (content, state.snapshot_type)
            source = "now"
        state = self.states.get(camera_id)
        if state is None:
            return
        state.alarm_image, state.alarm_image_type = frame
        state.alarm_image_at = dt_util.utcnow()
        state.alarm_image_source = source
        state.alarm_event_id = event_id
        async_dispatcher_send(self.hass, self.signal_update(camera_id))

    async def async_event_image(self, event_id: int) -> tuple[bytes, str] | None:
        """The saved frame of an event (`events:read`); None when there is none."""
        if (cached := self._frames.get(event_id)) is not None:
            self._frames.move_to_end(event_id)
            return cached
        if not self.has(SCOPE_EVENTS) or self.bridge.auth_failed:
            return None
        try:
            content, content_type = await self.client.async_get_image(
                f"/events/{event_id}/snapshot"
            )
        except ProstoCamScopeError as err:
            self._deny(SCOPE_EVENTS, err)
            return None
        except ProstoCamAuthError:
            self.bridge.report_auth_failed()
            return None
        except ProstoCamError as err:
            if getattr(err, "code", None) != ERROR_FRAME_MISSING:
                self.stats["snapshot_errors"] += 1
            LOGGER.debug("ProstoCAM event %s: no frame (%s)", event_id, err)
            return None
        if not content:
            return None
        self.stats["event_frames"] = self.stats.get("event_frames", 0) + 1
        self._frames[event_id] = (content, content_type)
        while len(self._frames) > FRAME_CACHE_SIZE:
            self._frames.popitem(last=False)
        return content, content_type

    def _handle_status(self, camera: Any) -> None:
        if not isinstance(camera, dict):
            return
        camera_id = self.camera_for(camera.get("id"))
        if camera_id is None:
            self.stats["unknown_camera_events"] += 1
            return
        state = self.states.setdefault(camera_id, CameraState())
        video = camera.get("video")
        if isinstance(video, dict):
            video_state = _str(video.get("state"))
            if video_state is not None:
                state.video_state = video_state
                state.online = video_state == "online"
            streaming = video.get("streaming")
            if isinstance(streaming, bool):
                state.streaming = streaming
        state.last_frame_at = _str(camera.get("last_frame_at")) or state.last_frame_at
        async_dispatcher_send(self.hass, self.signal_update(camera_id))

    def _handle_incident(self, name: str, incident: Any) -> None:
        if not isinstance(incident, dict):
            return
        camera_id = self.camera_for(incident.get("camera_id"))
        if camera_id is None:
            return
        state = self.states.setdefault(camera_id, CameraState())
        if name == "incident.closed":
            state.incident = None
            return
        state.incident = {
            "id": _int(incident.get("id")),
            "status": _str(incident.get("status")),
            "priority": _str(incident.get("priority")),
            "activity": _str(incident.get("activity")),
        }

    def _handle_problem(self, problem: Any, *, opened: bool) -> None:
        if not isinstance(problem, dict):
            return
        camera_id = self.camera_for(problem.get("camera_id"))
        kind = _str(problem.get("kind"))
        if camera_id is None or kind is None:
            return
        problems = self.states.setdefault(camera_id, CameraState()).problems
        if opened:
            problems.add(kind)
        else:
            problems.discard(kind)

    # --------------------------------------------------------------- helpers

    def _data_to_save(self) -> dict[str, Any]:
        return {"cursor": self.cursor}

    def diagnostics(self) -> dict[str, Any]:
        """State for diagnostics: no token, no live key, no live address."""
        return {
            "protocol": self.protocol,
            "capabilities": sorted(self.capabilities) if self.capabilities is not None else None,
            "missing_access": self.missing_scopes,
            "denied": sorted(self.denied),
            "catalog_loaded": self.catalog_loaded,
            "has_cursor": self.cursor is not None,
            "cameras": [
                {
                    "id": camera_id,
                    "name": item.get("name"),
                    "manufacturer": item.get("manufacturer"),
                    "model": item.get("model"),
                    "zone": item.get("zone"),
                    "online": item.get("online"),
                    "streaming": item.get("streaming"),
                    "features": item.get("features"),
                    "state": self._state_diagnostics(camera_id),
                }
                for camera_id, item in sorted(self.cameras.items())
            ],
            "stats": dict(self.stats),
            "control": self.control.diagnostics() if self.control is not None else None,
        }

    def _state_diagnostics(self, camera_id: int) -> dict[str, Any] | None:
        state = self.states.get(camera_id)
        if state is None:
            return None
        return {
            "online": state.online,
            "streaming": state.streaming,
            "video_state": state.video_state,
            "detections": dict(state.detections),
            "last_alarm": state.last_alarm,
            "incident": state.incident,
            "problems": sorted(state.problems),
            "alarm_image_at": state.alarm_image_at.isoformat()
            if state.alarm_image_at
            else None,
            "alarm_image_source": state.alarm_image_source,
            "alarm_event_id": state.alarm_event_id,
            "last_event_id": state.last_event_id,
            "muted": state.muted,
            "muted_until": state.muted_until,
            "deter_capable": state.deter_capable,
            "deter_actions": list(state.deter_actions),
            "snapshot_cached": state.snapshot is not None,
        }


def async_add_camera_entities(
    hub: ProstoCamCameras,
    entry: ConfigEntry,
    build: Callable[[int], list[Any]],
    add: Callable[[list[Any]], None],
) -> None:
    """Add entities for the cameras of the catalog now and for every new one."""

    @callback
    def _add(camera_ids: list[int]) -> None:
        entities = [entity for camera_id in camera_ids for entity in build(camera_id)]
        if entities:
            add(entities)

    _add(list(hub.cameras))
    entry.async_on_unload(async_dispatcher_connect(hub.hass, hub.signal_new, _add))
