"""Protocol 3 of ProstoCAM in Home Assistant: arming, buttons, mute and money.

Contract §11–§12. Everything here works through the areas the subscriber ticked
in the web account; none of them is given by default:

* `arming:write` — the ProstoCAM arming as an alarm panel of Home Assistant.
  The state is read at start, on every `arming.changed` of the event channel
  and every minute while the channel is down. Commands carry the last known
  `revision`; a refusal "not ready" names the cameras. An optional two-way
  sync with an alarm panel of Home Assistant (off by default) mirrors only
  changes the other side made: a change ProstoCAM marks `source: ha` is never
  mirrored back, a mode we just set on the panel of Home Assistant is not sent
  back for 15 s, and the server itself answers `unchanged` to the same mode;
* `actions:write` — test alarm, "Check with AI" (a credit is spent only after
  a second press or with `spend_credit: true` of the service), mute of a
  camera, deterrence where the device has an output;
* `account:read` — balance, tariff, AI credits, next charge (every 15 min) and
  a repair issue while the account is restricted or suspended.

Neither the token nor the addresses of video reach the log or diagnostics.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import timedelta
import time
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.components import persistent_notification
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.translation import async_get_translations

from .api import (
    ProstoCamAuthError,
    ProstoCamClient,
    ProstoCamError,
    ProstoCamRejectedError,
    ProstoCamScopeError,
    ProstoCamUnavailableError,
)
from .cameras import CameraState
from .const import (
    ACCOUNT_INTERVAL,
    ACCOUNT_STAGES_TO_FIX,
    AI_CONFIRM_WINDOW,
    ARMING_POLICY_DEGRADED,
    ARMING_POLL,
    ARMING_REFRESH,
    ARMING_STATES,
    CONF_ARMING_SYNC,
    CONF_ARMING_SYNC_ENTITY,
    DEFAULT_TITLE,
    DETERRENCE_INTERVAL,
    DOMAIN,
    ERROR_AI_CONFIRM,
    ERROR_ARMING_NOT_READY,
    ERROR_ECHO_SUPPRESSED,
    ERROR_UNCOVERED,
    ERROR_VERSION_CONFLICT,
    EVENT_AI_VERDICT,
    HA_PANEL_TO_ARMING,
    ISSUE_ACCOUNT_STAGE,
    LOGGER,
    MAX_MUTE_MINUTES,
    MUTE_INTERVAL,
    PATH_ACCOUNT,
    PATH_ARMING,
    SCOPE_ACCOUNT,
    SCOPE_ACTIONS,
    SCOPE_ARMING,
    SYNC_ECHO_WINDOW,
    TRIGGERED_HOLD,
)
from .errors import command_error
from .words import alarm_label

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .bridge import ProstoCamBridge
    from .cameras import ProstoCamCameras

# Services of Home Assistant panels for the modes of ProstoCAM.
PANEL_SERVICE: dict[str, str] = {
    "disarmed": "alarm_disarm",
    "armed_home": "alarm_arm_home",
    "armed_night": "alarm_arm_night",
    "armed_away": "alarm_arm_away",
}

ACCOUNT_SENSORS = ("balance", "tariff", "ai_credits", "next_charge", "stage")


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None



class ProstoCamControl:
    """Arming, buttons, mute and money of one config entry (protocol 3)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ProstoCamClient,
        bridge: ProstoCamBridge,
        cameras: ProstoCamCameras,
    ) -> None:
        """Prepare; nothing runs until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self.bridge = bridge
        self.cameras = cameras
        self.uid = cameras.uid
        self.arming: dict[str, Any] | None = None
        self.account: dict[str, Any] | None = None
        self.started = False
        self._clock = time.monotonic
        self._arming_read_at: float | None = None
        self._triggered_until: float | None = None
        self._triggered_unsub: CALLBACK_TYPE | None = None
        # The mode we set on the Home Assistant panel (sync), to recognise its echo.
        self._mirrored: tuple[str, float] | None = None
        # camera id -> monotonic deadline of the second press of "Check with AI".
        self._ai_confirm: dict[int, float] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._stopped = False
        self.stats: dict[str, Any] = {
            "arming_reads": 0,
            "arming_errors": 0,
            "arming_commands": 0,
            "arming_refusals": {},
            "sync_to_prostocam": 0,
            "sync_to_home_assistant": 0,
            "sync_echo_skipped": 0,
            "account_reads": 0,
            "account_errors": 0,
            "mute_errors": 0,
            "deterrence_errors": 0,
            "ai_checks": 0,
            "error_codes": {},
        }

    # ------------------------------------------------------------- signals

    @property
    def signal_update(self) -> str:
        """Dispatcher signal: arming or account changed."""
        return f"{DOMAIN}_{self.entry.entry_id}_control"

    # -------------------------------------------------------------- access

    @property
    def arming_enabled(self) -> bool:
        """The ProstoCAM arming is an alarm panel of Home Assistant."""
        return self.cameras.has_control(SCOPE_ARMING)

    @property
    def account_enabled(self) -> bool:
        """Money sensors exist."""
        return self.cameras.has_control(SCOPE_ACCOUNT)

    @property
    def sync_entity(self) -> str | None:
        """The Home Assistant panel the ProstoCAM arming follows (option, off by default)."""
        if not self.arming_enabled or not self.entry.options.get(CONF_ARMING_SYNC):
            return None
        entity_id = self.entry.options.get(CONF_ARMING_SYNC_ENTITY)
        return entity_id if isinstance(entity_id, str) and entity_id else None

    def expected_unique_ids(self) -> set[str]:
        """Unique ids of the entities of the account device the access allows."""
        expected: set[str] = set()
        if self.arming_enabled:
            expected.add(self.unique_id("arming"))
        if self.account_enabled:
            expected |= {self.unique_id(f"account_{key}") for key in ACCOUNT_SENSORS}
        return expected

    def unique_id(self, key: str) -> str:
        """Unique id of an entity of the account (not of a camera)."""
        return f"{self.uid}_{key}"

    @property
    def device_identifier(self) -> str:
        """Identifier of the device of the ProstoCAM account."""
        return f"{self.uid}_account"

    def device_info(self) -> DeviceInfo:
        """The Home Assistant device of the ProstoCAM account."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_identifier)},
            name=self.entry.title or DEFAULT_TITLE,
            manufacturer=DEFAULT_TITLE,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=self.client.server,
        )

    # ----------------------------------------------------------- life cycle

    async def async_start(self) -> None:
        """Read the arming and the account; start the timers the access needs."""
        self.started = True
        if not self.arming_enabled and not self.account_enabled:
            self._remove_account_device()
        if self.arming_enabled:
            await self.async_refresh_arming()
            self._unsubs.append(
                async_track_time_interval(
                    self.hass,
                    self._async_arming_tick,
                    timedelta(seconds=ARMING_POLL),
                    cancel_on_shutdown=True,
                )
            )
            if (entity_id := self.sync_entity) is not None:
                self._unsubs.append(
                    async_track_state_change_event(
                        self.hass, [entity_id], self._async_panel_changed
                    )
                )
        if self.account_enabled:
            await self.async_refresh_account()
            self._unsubs.append(
                async_track_time_interval(
                    self.hass,
                    self._async_account_tick,
                    timedelta(seconds=ACCOUNT_INTERVAL),
                    cancel_on_shutdown=True,
                )
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ACCOUNT_STAGE)
        if self.cameras.camera_actions:
            self._unsubs.append(
                async_track_time_interval(
                    self.hass,
                    self._async_mute_tick,
                    timedelta(seconds=MUTE_INTERVAL),
                    cancel_on_shutdown=True,
                )
            )
            self._unsubs.append(
                async_track_time_interval(
                    self.hass,
                    self._async_deterrence_tick,
                    timedelta(seconds=DETERRENCE_INTERVAL),
                    cancel_on_shutdown=True,
                )
            )

    async def async_stop(self) -> None:
        """Stop timers and listeners."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._triggered_unsub is not None:
            self._triggered_unsub()
            self._triggered_unsub = None
        for task in list(self._tasks):
            if not task.done():
                task.cancel()

    def _remove_account_device(self) -> None:
        dev_reg = dr.async_get(self.hass)
        identifier = (DOMAIN, self.device_identifier)
        for device in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            if identifier in device.identifiers:
                dev_reg.async_remove_device(device.id)

    def _spawn(self, coro: Any, name: str) -> None:
        task = self.entry.async_create_task(self.hass, coro, name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _update(self) -> None:
        async_dispatcher_send(self.hass, self.signal_update)

    def _failed(self, scope: str, err: ProstoCamError) -> None:
        """Common handling of a refused read: lost area, revoked token."""
        if isinstance(err, ProstoCamScopeError):
            self.cameras.deny(scope, err)
        elif isinstance(err, ProstoCamAuthError):
            self.bridge.report_auth_failed()

    # --------------------------------------------------------------- arming

    async def _async_arming_tick(self, _now: Any) -> None:
        connected = bool(self.cameras.stats.get("stream_connected"))
        if (
            connected
            and self._arming_read_at is not None
            and self._clock() - self._arming_read_at < ARMING_REFRESH
        ):
            return  # `arming.changed` of the channel keeps it fresh
        await self.async_refresh_arming()

    async def async_refresh_arming(self) -> dict[str, Any] | None:
        """Read the arming of ProstoCAM; None when it could not be read."""
        if self._stopped or not self.arming_enabled or self.bridge.auth_failed:
            return None
        try:
            data = await self.client.async_call("get", PATH_ARMING)
        except ProstoCamError as err:
            self._failed(SCOPE_ARMING, err)
            self.stats["arming_errors"] += 1
            LOGGER.debug("ProstoCAM arming not read: %s", err)
            return None
        self.stats["arming_reads"] += 1
        self._apply_arming(data)
        return data

    @callback
    def handle_arming_changed(self, arming: Any) -> None:
        """`arming.changed` of the event channel: read our view of it unless already known."""
        if not self.arming_enabled or self._stopped:
            return
        if isinstance(arming, dict) and self.arming is not None:
            revision = _int(arming.get("revision"))
            if (
                revision is not None
                and revision == self.arming.get("revision")
                and (arming.get("pending") is None) == (self.arming.get("pending") is None)
            ):
                return  # our own command: its answer already brought this revision
        self._spawn(self.async_refresh_arming(), "prostocam_arming")

    def _apply_arming(self, data: dict[str, Any]) -> None:
        if not isinstance(data.get("state"), str):
            return
        old = self.arming
        self.arming = data
        self._arming_read_at = self._clock()
        if data.get("state") == "disarmed":
            self._triggered_until = None
        self._update()
        self._mirror_to_panel(old, data)

    @property
    def arming_state(self) -> str | None:
        """`disarmed`, `armed_*`, `arming` or `triggered`; None when unknown."""
        if self.arming is None:
            return None
        state = self.arming.get("state")
        if (
            self._triggered_until is not None
            and self._clock() < self._triggered_until
            and state not in (None, "disarmed")
        ):
            return "triggered"
        return state if isinstance(state, str) else None

    @callback
    def note_alarm(self, *, test: bool) -> None:
        """A real alarm while armed turns the panel `triggered` for two minutes."""
        if test or self.arming is None or self.arming.get("state") in (None, "disarmed"):
            return
        if not self.arming_enabled:
            return
        self._triggered_until = self._clock() + TRIGGERED_HOLD
        if self._triggered_unsub is not None:
            self._triggered_unsub()

        @callback
        def _end(_now: Any) -> None:
            self._triggered_unsub = None
            self._update()

        self._triggered_unsub = async_call_later(self.hass, TRIGGERED_HOLD + 1, _end)
        self._update()

    async def async_set_arming(
        self,
        state: str,
        *,
        policy: str | None = None,
        acknowledged: list[int] | None = None,
    ) -> dict[str, Any]:
        """Change the ProstoCAM arming; raises `HomeAssistantError` with the reason."""
        if state not in ARMING_STATES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="arming_state_unknown",
                translation_placeholders={"state": state},
            )
        body: dict[str, Any] = {"state": state}
        if policy is not None:
            body["policy"] = policy
            body["acknowledged_uncovered"] = acknowledged or []
        revision = _int(self.arming.get("revision")) if self.arming else None
        if revision is not None:
            body["expected_revision"] = revision
        self.stats["arming_commands"] += 1
        try:
            data = await self.client.async_call("post", PATH_ARMING, body)
        except ProstoCamRejectedError as err:
            refusals: dict[str, int] = self.stats["arming_refusals"]
            refusals[str(err.code)] = refusals.get(str(err.code), 0) + 1
            if err.code in (ERROR_ARMING_NOT_READY, ERROR_UNCOVERED):
                raise await self._not_ready_error(state, err) from err
            if err.code in (ERROR_VERSION_CONFLICT, ERROR_ECHO_SUPPRESSED):
                # Home Assistant acted on a stale state: take ours, do not repeat.
                await self.async_refresh_arming()
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="arming_conflict"
                ) from err
            raise command_error(err, self.stats["error_codes"]) from err
        except ProstoCamError as err:
            self._failed(SCOPE_ARMING, err)
            raise command_error(err, self.stats["error_codes"]) from err
        arming = data.get("arming")
        if isinstance(arming, dict):
            self._apply_arming(arming)
        return data

    async def async_arm_anyway(self, state: str) -> dict[str, Any]:
        """Arm although some cameras are not ready, naming each of them (policy degraded)."""
        cameras = await self._not_ready_cameras(state, None)
        acknowledged = [camera_id for camera_id, _name, _why in cameras]
        try:
            return await self.async_set_arming(
                state, policy=ARMING_POLICY_DEGRADED, acknowledged=acknowledged
            )
        except HomeAssistantError as err:
            # The list changed between the read and the command: one more try with it.
            uncovered = getattr(err.__cause__, "detail", {}).get("uncovered")
            if not isinstance(uncovered, list) or not uncovered:
                raise
            ids = [i for i in (_int(item) for item in uncovered) if i is not None]
            return await self.async_set_arming(
                state, policy=ARMING_POLICY_DEGRADED, acknowledged=sorted(set(ids))
            )

    async def _not_ready_cameras(
        self, state: str, err: ProstoCamRejectedError | None
    ) -> list[tuple[int, str, str | None]]:
        """Cameras that keep the arming from being ready: (id, name, why)."""
        cameras: dict[int, tuple[int, str, str | None]] = {}
        try:
            data = await self.client.async_call("get", PATH_ARMING, params={"state": state})
        except ProstoCamError as read_err:
            LOGGER.debug("ProstoCAM readiness not read: %s", read_err)
            data = {}
        readiness = data.get("readiness")
        not_ready = readiness.get("not_ready") if isinstance(readiness, dict) else None
        if isinstance(not_ready, list):
            for item in not_ready:
                if not isinstance(item, dict) or (camera_id := _int(item.get("camera_id"))) is None:
                    continue
                name = item.get("name")
                why = item.get("why")
                cameras[camera_id] = (
                    camera_id,
                    name if isinstance(name, str) and name else self.cameras.camera_name(camera_id),
                    why if isinstance(why, str) else None,
                )
        uncovered = err.detail.get("uncovered") if err is not None else None
        if isinstance(uncovered, list):
            for raw in uncovered:
                camera_id = _int(raw)
                if camera_id is not None and camera_id not in cameras:
                    cameras[camera_id] = (camera_id, self.cameras.camera_name(camera_id), None)
        return sorted(cameras.values())

    async def _not_ready_error(
        self, state: str, err: ProstoCamRejectedError
    ) -> HomeAssistantError:
        """The refusal "not ready" with the list of cameras, also as a notification."""
        cameras = await self._not_ready_cameras(state, err)
        listed = ", ".join(
            f"{name} ({why})" if why else name for _camera_id, name, why in cameras
        ) or (err.message or str(err.code))
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "exceptions", {DOMAIN}
        )
        template = translations.get(
            f"component.{DOMAIN}.exceptions.arming_not_ready.message",
            "ProstoCAM is not ready to arm: {cameras}",
        )
        persistent_notification.async_create(
            self.hass,
            template.replace("{cameras}", listed)
            + ("\n\n" + err.message if err.message else ""),
            title=self.entry.title or DEFAULT_TITLE,
            notification_id=f"{DOMAIN}_{self.entry.entry_id}_arming_not_ready",
        )
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="arming_not_ready",
            translation_placeholders={"cameras": listed},
        )

    # ------------------------------------------------- sync with an HA panel

    def _mirror_to_panel(self, old: dict[str, Any] | None, new: dict[str, Any]) -> None:
        """A change made in ProstoCAM (not by this Home Assistant) goes to the HA panel."""
        entity_id = self.sync_entity
        if entity_id is None or old is None:
            return  # the first read is not a change: nothing is pushed at start
        if new.get("source") == "ha" or old.get("revision") == new.get("revision"):
            return
        target = new.get("state")
        if target not in PANEL_SERVICE:
            return  # `arming`: the landing of the countdown comes as its own change
        panel = self.hass.states.get(entity_id)
        if panel is None or panel.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        if HA_PANEL_TO_ARMING.get(panel.state) == target:
            return
        self._mirrored = (target, self._clock())
        self.stats["sync_to_home_assistant"] += 1
        self._spawn(self._async_call_panel(entity_id, target), "prostocam_sync_panel")

    async def _async_call_panel(self, entity_id: str, target: str) -> None:
        try:
            await self.hass.services.async_call(
                "alarm_control_panel",
                PANEL_SERVICE[target],
                {"entity_id": entity_id},
                blocking=True,
            )
        except HomeAssistantError as err:
            # A panel that needs a code can not follow without one.
            LOGGER.warning("ProstoCAM arming not mirrored to %s: %s", entity_id, err)

    @callback
    def _async_panel_changed(self, event: Event) -> None:
        """The HA panel changed its mode: ProstoCAM follows (with the revision)."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or old_state is None or new_state.state == old_state.state:
            return
        target = HA_PANEL_TO_ARMING.get(new_state.state)
        if target is None:
            return  # arming, pending, triggered …: not a mode
        if (
            self._mirrored is not None
            and self._mirrored[0] == target
            and self._clock() - self._mirrored[1] < SYNC_ECHO_WINDOW
        ):
            self.stats["sync_echo_skipped"] += 1
            return  # the echo of our own mirroring
        if self.arming is not None:
            pending = self.arming.get("pending")
            pending_state = pending.get("state") if isinstance(pending, dict) else None
            if target in (self.arming.get("state"), pending_state):
                return
        self.stats["sync_to_prostocam"] += 1
        self._spawn(self._async_sync_to_prostocam(target), "prostocam_sync")

    async def _async_sync_to_prostocam(self, target: str) -> None:
        try:
            await self.async_set_arming(target)
        except HomeAssistantError as err:
            LOGGER.warning("ProstoCAM did not follow the Home Assistant panel: %s", err)

    # -------------------------------------------------------------- account

    async def _async_account_tick(self, _now: Any) -> None:
        await self.async_refresh_account()

    async def async_refresh_account(self) -> None:
        """Balance, tariff, AI credits and the next charge."""
        if self._stopped or not self.account_enabled or self.bridge.auth_failed:
            return
        try:
            data = await self.client.async_call("get", PATH_ACCOUNT)
        except ProstoCamUnavailableError as err:
            # Billing is silent: keep the last known numbers.
            self.stats["account_errors"] += 1
            LOGGER.debug("ProstoCAM account not read: %s", err)
            return
        except ProstoCamError as err:
            self._failed(SCOPE_ACCOUNT, err)
            self.stats["account_errors"] += 1
            LOGGER.debug("ProstoCAM account not read: %s", err)
            return
        self.stats["account_reads"] += 1
        self.account = data
        stage = data.get("stage")
        if isinstance(stage, str) and stage in ACCOUNT_STAGES_TO_FIX:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_ACCOUNT_STAGE,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_ACCOUNT_STAGE,
                translation_placeholders={"stage": stage},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_ACCOUNT_STAGE)
        self._update()

    # ------------------------------------------------------------- cameras

    def _state(self, camera_id: int) -> CameraState:
        return self.cameras.states.setdefault(camera_id, CameraState())

    async def async_refresh_cameras(self, camera_ids: Iterable[int]) -> None:
        """Mute and deterrence outputs of these cameras (before their entities appear)."""
        ids = list(camera_ids)
        await asyncio.gather(
            *(self._async_read_mute(camera_id) for camera_id in ids),
            *(self._async_read_deterrence(camera_id) for camera_id in ids),
        )

    async def _async_mute_tick(self, _now: Any) -> None:
        await asyncio.gather(
            *(self._async_read_mute(camera_id) for camera_id in list(self.cameras.cameras))
        )

    async def _async_deterrence_tick(self, _now: Any) -> None:
        await asyncio.gather(
            *(
                self._async_read_deterrence(camera_id)
                for camera_id in list(self.cameras.cameras)
            )
        )

    async def _async_read_mute(self, camera_id: int) -> None:
        if self._stopped or not self.cameras.camera_actions or self.bridge.auth_failed:
            return
        try:
            data = await self.client.async_call("get", f"/cameras/{camera_id}/mute")
        except ProstoCamError as err:
            self._failed(SCOPE_ACTIONS, err)
            self.stats["mute_errors"] += 1
            LOGGER.debug("ProstoCAM camera %s: mute not read (%s)", camera_id, err)
            return
        self._apply_mute(camera_id, data)

    def _apply_mute(self, camera_id: int, data: dict[str, Any]) -> None:
        state = self._state(camera_id)
        muted = data.get("muted")
        if isinstance(muted, bool):
            state.muted = muted
            if not muted:
                state.mute_choice = None
        until = data.get("muted_until")
        state.muted_until = until if isinstance(until, str) else None
        async_dispatcher_send(self.hass, self.cameras.signal_update(camera_id))

    async def async_set_mute(
        self, camera_id: int, minutes: int, choice: str | None = None
    ) -> None:
        """Mute the alarms of a camera for `minutes` (0 = unmute)."""
        minutes = max(0, min(MAX_MUTE_MINUTES, int(minutes)))
        path = f"/cameras/{camera_id}/mute"
        try:
            if minutes == 0:
                data = await self.client.async_call("delete", path)
            else:
                data = await self.client.async_call("put", path, {"minutes": minutes})
        except ProstoCamError as err:
            self._failed(SCOPE_ACTIONS, err)
            raise command_error(err, self.stats["error_codes"]) from err
        self._apply_mute(camera_id, data)
        if minutes:
            self._state(camera_id).mute_choice = choice
            async_dispatcher_send(self.hass, self.cameras.signal_update(camera_id))

    async def _async_read_deterrence(self, camera_id: int) -> None:
        if self._stopped or not self.cameras.camera_actions or self.bridge.auth_failed:
            return
        try:
            data = await self.client.async_call("get", f"/cameras/{camera_id}/deterrence")
        except ProstoCamError as err:
            self._failed(SCOPE_ACTIONS, err)
            self.stats["deterrence_errors"] += 1
            LOGGER.debug("ProstoCAM camera %s: outputs not read (%s)", camera_id, err)
            return
        state = self._state(camera_id)
        actions = data.get("actions")
        state.deter_actions = (
            [action for action in actions if isinstance(action, str)]
            if isinstance(actions, list)
            else []
        )
        state.deter_capable = data.get("capable") is True and bool(state.deter_actions)
        async_dispatcher_send(self.hass, self.cameras.signal_update(camera_id))

    async def async_deter(self, camera_id: int) -> dict[str, Any]:
        """Deterrence of the camera (only after a fresh alarm, as in the web account)."""
        body: dict[str, Any] = {}
        if (event_id := self._state(camera_id).last_event_id) is not None:
            body["event_id"] = event_id
        try:
            return await self.client.async_call(
                "post", f"/cameras/{camera_id}/deterrence", body
            )
        except ProstoCamError as err:
            self._failed(SCOPE_ACTIONS, err)
            raise command_error(err, self.stats["error_codes"]) from err

    async def async_test_alarm(self, camera_id: int) -> dict[str, Any]:
        """A test alarm of the camera through every channel of the subscriber."""
        try:
            return await self.client.async_call("post", f"/cameras/{camera_id}/test-alarm")
        except ProstoCamError as err:
            self._failed(SCOPE_ACTIONS, err)
            raise command_error(err, self.stats["error_codes"]) from err

    async def async_verify_ai(
        self,
        camera_id: int | None,
        event_id: int | None = None,
        *,
        spend_credit: bool,
    ) -> dict[str, Any]:
        """AI check of an event; without `spend_credit` the server only names the price."""
        if event_id is None and camera_id is not None:
            event_id = self._state(camera_id).last_event_id
        if event_id is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_event")
        body: dict[str, Any] = {"spend_credit": True} if spend_credit else {}
        headers = {"Idempotency-Key": uuid.uuid4().hex} if spend_credit else None
        try:
            data = await self.client.async_call(
                "post", f"/events/{event_id}/ai-verify", body, headers=headers
            )
        except ProstoCamRejectedError as err:
            if err.code == ERROR_AI_CONFIRM:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="ai_confirm",
                    translation_placeholders={"message": _price_phrase(err.message)},
                ) from err
            raise command_error(err, self.stats["error_codes"]) from err
        except ProstoCamError as err:
            self._failed(SCOPE_ACTIONS, err)
            raise command_error(err, self.stats["error_codes"]) from err
        self.stats["ai_checks"] += 1
        camera = _int(data.get("camera_id"))
        if camera is not None:
            camera_id = self.cameras.camera_for(camera) or camera_id
        device = self.cameras.device_of(camera_id) if camera_id is not None else None
        self.hass.bus.async_fire(
            EVENT_AI_VERDICT,
            {
                "entry_id": self.entry.entry_id,
                "camera_id": camera_id,
                "camera_name": self.cameras.camera_name(camera_id)
                if camera_id is not None
                else None,
                "device_id": device.id if device is not None else None,
                "event_id": event_id,
                "status": data.get("status"),
                "classification": data.get("classification"),
                "confidence": data.get("confidence"),
                "label": alarm_label(
                    self.hass.config.language,
                    data.get("classification"),
                    data.get("confidence"),
                ),
                "words": data.get("words"),
                "phrase": data.get("phrase"),
                "snapshot": self.cameras.snapshot_path(event_id),
            },
        )
        return data

    async def async_press_ai(self, camera_id: int) -> None:
        """The button: the first press names the price, a second one within 30 s spends."""
        now = self._clock()
        deadline = self._ai_confirm.pop(camera_id, None)
        if deadline is not None and now < deadline:
            await self.async_verify_ai(camera_id, spend_credit=True)
            return
        try:
            await self.async_verify_ai(camera_id, spend_credit=False)
        except HomeAssistantError as err:
            if err.translation_key != "ai_confirm":
                raise
            self._ai_confirm[camera_id] = now + AI_CONFIRM_WINDOW
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="ai_press_again",
                translation_placeholders={
                    "message": (err.translation_placeholders or {}).get("message", ""),
                    "seconds": str(AI_CONFIRM_WINDOW),
                },
            ) from err

    # --------------------------------------------------------------- helpers

    def diagnostics(self) -> dict[str, Any]:
        """State for diagnostics: no money amounts, no addresses."""
        arming = self.arming or {}
        return {
            "arming_enabled": self.arming_enabled,
            "account_enabled": self.account_enabled,
            "sync_entity": self.sync_entity,
            "arming": {
                key: arming.get(key)
                for key in ("state", "mode", "armed", "revision", "changed_by", "source", "pending")
            }
            if self.arming is not None
            else None,
            "arming_state": self.arming_state,
            "account_stage": (self.account or {}).get("stage"),
            "stats": dict(self.stats),
        }


def _price_phrase(message: str | None) -> str:
    """The price of the AI check for a human, without the wording of the API.

    A server before 1372 says "…(1): send spend_credit: true to confirm": the
    name of a request field means nothing to the person at the button, and
    the integration says itself how to confirm (press again / the action field).
    """
    text = (message or "").strip()
    if "spend_credit" in text:
        text = text.split(":", 1)[0].strip()
    return text.rstrip(".")
