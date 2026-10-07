"""Bridge between the Home Assistant state machine and the ProstoCAM server.

What it does:

* sends the catalog of matching entities (domains and device classes chosen in
  the options) when Home Assistant has started and whenever the entity, device
  or area registry changes;
* listens to `state_changed` and queues every transition of an entity the
  subscriber enabled in the ProstoCAM web account, and of every alarm panel
  (the server returns the enabled list and its limits in each reply);
* sends the queue in order: sensors in batches to `/events`, alarm panel modes
  one by one to `/alarm`. A failed request stays in the queue and is retried
  with a backoff, a new event retries at once; sequence numbers make a resent
  batch harmless. The queue and the counter survive a restart;
* reports the mode of each alarm panel at start (`initial`, not an event);
* sends a heartbeat so the server knows Home Assistant is alive.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import timedelta
import hashlib
import json
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.const import EVENT_STATE_CHANGED, __version__ as HA_VERSION
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import (
    ProstoCamAuthError,
    ProstoCamClient,
    ProstoCamError,
    ProstoCamOutdatedError,
    ProstoCamRejectedError,
)
from .const import (
    ALARM_DOMAIN,
    BATCH_SIZE,
    CATALOG_COOLDOWN,
    CONF_DEVICE_CLASSES,
    CONF_DOMAINS,
    DEFAULT_DEVICE_CLASSES,
    DEFAULT_DOMAINS,
    DOMAIN,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_MAX,
    HEARTBEAT_MIN,
    ISSUE_OUTDATED,
    KEY_CATALOG_DOMAINS,
    KEY_CONFIG,
    KEY_ENABLED,
    KEY_EVENT_DOMAINS,
    KEY_HEARTBEAT_INTERVAL,
    KEY_MAX_BATCH,
    KEY_MAX_CATALOG,
    LOGGER,
    MAX_CATALOG,
    MAX_QUEUE,
    MAX_USER_LENGTH,
    PATH_ALARM,
    PATH_ENTITIES,
    PATH_EVENTS,
    PATH_HEARTBEAT,
    RETRY_MAX,
    RETRY_MIN,
    SAVE_DELAY,
    SKIP_STATES,
    STORAGE_VERSION,
    VERSION,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


def _str_list(value: Any) -> set[str] | None:
    if not isinstance(value, list):
        return None
    return {item for item in value if isinstance(item, str)}


class ProstoCamBridge:
    """Pushes catalog, events, alarm panel modes and heartbeats to ProstoCAM."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ProstoCamClient
    ) -> None:
        """Prepare the bridge; nothing runs until `async_start`."""
        self.hass = hass
        self.entry = entry
        self.client = client
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.queue: deque[dict[str, Any]] = deque()
        self.stream = ""
        self.seq = 0
        # What the server said in its last reply.
        self.enabled: set[str] | None = None
        self.catalog_domains: set[str] | None = None
        self.event_domains: set[str] | None = None
        self.max_batch = BATCH_SIZE
        self.max_catalog = MAX_CATALOG
        self.heartbeat_interval = HEARTBEAT_INTERVAL
        self.auth_failed = False
        self.outdated = False
        self.catalog_size = 0
        self.stats: dict[str, Any] = {
            "sent": 0,
            "dropped": 0,
            "rejected": 0,
            "last_success": None,
            "last_error": None,
            "last_error_at": None,
        }
        self._stopped = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._started_unsub: CALLBACK_TYPE | None = None
        self._heartbeat_unsub: CALLBACK_TYPE | None = None
        self._retry_unsub: CALLBACK_TYPE | None = None
        self._retry_delay = 0.0
        self._split_until = 0
        self._flush_task: asyncio.Task[None] | None = None
        self._catalog_hash: str | None = None
        self._catalog_pending = False
        self._hass_started = False
        self._catalog_debouncer = Debouncer(
            hass,
            LOGGER,
            cooldown=CATALOG_COOLDOWN,
            immediate=False,
            function=self.async_send_catalog,
        )

    # ------------------------------------------------------------------ filter

    @property
    def domains(self) -> set[str]:
        """Domains the subscriber chose to share."""
        return set(self.entry.options.get(CONF_DOMAINS, DEFAULT_DOMAINS))

    @property
    def device_classes(self) -> set[str]:
        """Binary sensor device classes the subscriber chose to share."""
        return set(self.entry.options.get(CONF_DEVICE_CLASSES, DEFAULT_DEVICE_CLASSES))

    def matches(self, state: State, server_domains: set[str] | None = None) -> bool:
        """Whether an entity may be shared with ProstoCAM at all."""
        if state.domain not in self.domains:
            return False
        if server_domains is not None and state.domain not in server_domains:
            return False
        if state.domain == "binary_sensor":
            return state.attributes.get("device_class") in self.device_classes
        return True

    # -------------------------------------------------------------- life cycle

    async def async_start(self) -> None:
        """Restore the queue, check the token and start listening.

        Raises `ProstoCamAuthError` when the server rejects the token; a server
        that can not be reached does not stop the start (events are queued).
        """
        stored = await self._store.async_load() or {}
        self.stream = str(stored.get("stream") or uuid.uuid4().hex)
        self.seq = int(stored.get("seq") or 0)
        for item in stored.get("pending") or []:
            if isinstance(item, dict) and isinstance(item.get("seq"), int):
                self.queue.append(item)
        while len(self.queue) > MAX_QUEUE:
            self.queue.popleft()
            self.stats["dropped"] += 1

        await self.async_heartbeat(raise_auth=True)

        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._async_state_changed)
        )
        for event_type in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            ar.EVENT_AREA_REGISTRY_UPDATED,
        ):
            self._unsubs.append(
                self.hass.bus.async_listen(event_type, self._async_registry_updated)
            )
        self._reschedule_heartbeat()
        self._started_unsub = async_at_started(self.hass, self._async_hass_started)

    async def _async_hass_started(self, _hass: HomeAssistant) -> None:
        self._started_unsub = None
        self._hass_started = True
        await self.async_send_catalog()
        # The current mode of every alarm panel: remembered by the server, not an event.
        for state in self.hass.states.async_all(ALARM_DOMAIN):
            if self.matches(state) and state.state not in SKIP_STATES:
                self._enqueue(state, None, initial=True)
        self._schedule_flush()

    async def async_stop(self) -> None:
        """Stop listening and keep the unsent queue for the next start."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for name in ("_started_unsub", "_heartbeat_unsub", "_retry_unsub"):
            unsub = getattr(self, name)
            if unsub is not None:
                unsub()
                setattr(self, name, None)
        self._catalog_debouncer.async_shutdown()
        task = self._flush_task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._store.async_save(self._data_to_save())

    # ------------------------------------------------------------------ catalog

    def build_catalog(self) -> list[dict[str, Any]]:
        """Describe every entity that matches the options."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        catalog: list[dict[str, Any]] = []
        for state in self.hass.states.async_all(sorted(self.domains)):
            if not self.matches(state, self.catalog_domains):
                continue
            entity = ent_reg.async_get(state.entity_id)
            device = (
                dev_reg.async_get(entity.device_id)
                if entity is not None and entity.device_id
                else None
            )
            area_id = (entity.area_id if entity is not None else None) or (
                device.area_id if device is not None else None
            )
            area = area_reg.async_get_area(area_id) if area_id else None
            catalog.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.name,
                    "device_class": state.attributes.get("device_class"),
                    "area": area.name if area is not None else None,
                    "manufacturer": device.manufacturer if device is not None else None,
                    "model": device.model if device is not None else None,
                    "platform": entity.platform if entity is not None else None,
                }
            )
        catalog.sort(key=lambda item: item["entity_id"])
        return catalog[: self.max_catalog]

    async def async_send_catalog(self) -> None:
        """Send the catalog unless the server already has the same one."""
        if self._stopped or self.auth_failed or self.outdated:
            return
        catalog = self.build_catalog()
        digest = hashlib.sha256(
            json.dumps(catalog, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if digest == self._catalog_hash:
            return
        try:
            data = await self.client.async_post(PATH_ENTITIES, {"entities": catalog})
        except ProstoCamError as err:
            self._catalog_pending = True
            self._handle_error(err)
            return
        self._catalog_hash = digest
        self._catalog_pending = False
        self.catalog_size = len(catalog)
        self._note_success()
        self._apply_server_data(data)

    @callback
    def _async_registry_updated(self, _event: Event) -> None:
        if self._hass_started:
            self._catalog_debouncer.async_schedule_call()

    # ------------------------------------------------------------------- events

    @callback
    def _async_state_changed(self, event: Event) -> None:
        new_state: State | None = event.data.get("new_state")
        old_state: State | None = event.data.get("old_state")
        if new_state is None or not self.matches(new_state):
            return
        if old_state is None:
            # A new entity: the catalog changed, the appearance is not an event.
            if self._hass_started:
                self._catalog_debouncer.async_schedule_call()
            return
        if new_state.state == old_state.state:
            return
        if new_state.state in SKIP_STATES or old_state.state in SKIP_STATES:
            return
        if self.event_domains is not None and new_state.domain not in self.event_domains:
            return
        if new_state.domain != ALARM_DOMAIN and (
            self.enabled is None or new_state.entity_id not in self.enabled
        ):
            return
        self._enqueue(new_state, old_state)

    def _enqueue(
        self, new_state: State, old_state: State | None, *, initial: bool = False
    ) -> None:
        self.seq += 1
        item: dict[str, Any] = {
            "seq": self.seq,
            "entity_id": new_state.entity_id,
            "from_state": old_state.state if old_state is not None else None,
            "to_state": new_state.state,
            "friendly_name": new_state.name,
            "changed_at": new_state.last_changed.isoformat(),
        }
        if new_state.domain == ALARM_DOMAIN:
            changed_by = new_state.attributes.get("changed_by")
            if isinstance(changed_by, str) and changed_by.strip():
                item["user"] = changed_by.strip()[:MAX_USER_LENGTH]
            if initial:
                item["initial"] = True
        else:
            item["device_class"] = new_state.attributes.get("device_class")
        if len(self.queue) >= MAX_QUEUE:
            self.queue.popleft()
            self.stats["dropped"] += 1
        self.queue.append(item)
        self._schedule_save()
        self._schedule_flush()

    @callback
    def _schedule_flush(self) -> None:
        if self._stopped or self.auth_failed or self.outdated or not self.queue:
            return
        if self._flush_task is not None and not self._flush_task.done():
            return  # the running flush picks up new items
        if self._retry_unsub is not None:
            # A new event does not wait for the backoff.
            self._retry_unsub()
            self._retry_unsub = None
        self._flush_task = self.entry.async_create_task(
            self.hass, self._async_flush(), "prostocam_flush"
        )

    def _next_request(self) -> tuple[str, dict[str, Any], int]:
        """Path, body and the last sequence number of the next request."""
        head = self.queue[0]
        if head["entity_id"].startswith(f"{ALARM_DOMAIN}."):
            body = {"stream": self.stream, "seq": head["seq"], "entity_id": head["entity_id"]}
            body["state"] = head["to_state"]
            for key in ("from_state", "friendly_name", "user", "changed_at", "initial"):
                if head.get(key) is not None:
                    body[key] = head[key]
            return PATH_ALARM, body, head["seq"]
        limit = 1 if head["seq"] <= self._split_until else self.max_batch
        batch: list[dict[str, Any]] = []
        for item in self.queue:
            if item["entity_id"].startswith(f"{ALARM_DOMAIN}.") or len(batch) >= limit:
                break
            batch.append(item)
        return PATH_EVENTS, {"stream": self.stream, "events": batch}, batch[-1]["seq"]

    async def _async_flush(self) -> None:
        while self.queue and not self._stopped:
            path, body, last_seq = self._next_request()
            count = len(body["events"]) if path == PATH_EVENTS else 1
            try:
                data = await self.client.async_post(path, body)
            except ProstoCamRejectedError as err:
                self._note_error(err)
                if count > 1:
                    # One bad event refuses the whole batch: find it one by one.
                    self._split_until = last_seq
                    continue
                LOGGER.warning("ProstoCAM refused an event (%s); it is dropped", err)
                self.stats["rejected"] += 1
                self._remove_upto(last_seq)
                self._schedule_save()
                continue
            except ProstoCamError as err:
                if self._handle_error(err):
                    self._schedule_retry()
                return
            self._remove_upto(last_seq)
            self.stats["sent"] += count
            self._retry_delay = 0.0
            self._note_success()
            self._apply_server_data(data)
            self._schedule_save()

    def _remove_upto(self, seq: int) -> None:
        while self.queue and self.queue[0]["seq"] <= seq:
            self.queue.popleft()

    def _schedule_retry(self) -> None:
        self._retry_delay = min(RETRY_MAX, max(RETRY_MIN, self._retry_delay * 2))
        LOGGER.debug("ProstoCAM unreachable, retry in %s s", self._retry_delay)
        self._retry_unsub = async_call_later(
            self.hass, self._retry_delay, self._async_retry
        )

    @callback
    def _async_retry(self, _now: Any) -> None:
        self._retry_unsub = None
        self._schedule_flush()

    # ---------------------------------------------------------------- heartbeat

    def _reschedule_heartbeat(self) -> None:
        if self._heartbeat_unsub is not None:
            self._heartbeat_unsub()
        self._heartbeat_unsub = async_track_time_interval(
            self.hass,
            self._async_heartbeat_tick,
            timedelta(seconds=self.heartbeat_interval),
            cancel_on_shutdown=True,
        )

    async def _async_heartbeat_tick(self, _now: Any) -> None:
        await self.async_heartbeat()

    async def async_heartbeat(self, *, raise_auth: bool = False) -> None:
        """Tell the server Home Assistant is alive and refresh the enabled list."""
        if self._stopped or self.auth_failed:
            return
        payload = {"client_version": VERSION, "ha_version": HA_VERSION}
        try:
            data = await self.client.async_post(PATH_HEARTBEAT, payload)
        except ProstoCamAuthError:
            if raise_auth:
                raise
            self._async_auth_failed()
            return
        except ProstoCamError as err:
            self._handle_error(err)
            return
        if self.outdated:
            # The server accepts this version again (an update, or a lower minimum).
            self.outdated = False
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_OUTDATED)
        self._note_success()
        self._apply_server_data(data)
        if self._catalog_pending and self._hass_started:
            await self.async_send_catalog()
        self._schedule_flush()

    # ------------------------------------------------------------------ helpers

    def _apply_server_data(self, data: dict[str, Any]) -> None:
        config = data.get(KEY_CONFIG)
        if not isinstance(config, dict):
            return
        enabled = _str_list(config.get(KEY_ENABLED))
        if enabled is not None:
            self.enabled = enabled
        catalog_domains = _str_list(config.get(KEY_CATALOG_DOMAINS))
        if catalog_domains is not None:
            self.catalog_domains = catalog_domains
        event_domains = _str_list(config.get(KEY_EVENT_DOMAINS))
        if event_domains is not None:
            self.event_domains = event_domains
        max_batch = config.get(KEY_MAX_BATCH)
        if isinstance(max_batch, int) and max_batch >= 1:
            self.max_batch = min(BATCH_SIZE, max_batch)
        max_catalog = config.get(KEY_MAX_CATALOG)
        if isinstance(max_catalog, int) and max_catalog >= 1:
            self.max_catalog = min(MAX_CATALOG, max_catalog)
        interval = config.get(KEY_HEARTBEAT_INTERVAL)
        if (
            isinstance(interval, int)
            and HEARTBEAT_MIN <= interval <= HEARTBEAT_MAX
            and interval != self.heartbeat_interval
        ):
            self.heartbeat_interval = interval
            if self._heartbeat_unsub is not None:
                self._reschedule_heartbeat()

    def _handle_error(self, err: ProstoCamError) -> bool:
        """Record an error; True when the request should be retried later."""
        self._note_error(err)
        if isinstance(err, ProstoCamAuthError):
            self._async_auth_failed()
            return False
        if isinstance(err, ProstoCamOutdatedError):
            self._async_outdated()
            return False
        if isinstance(err, ProstoCamRejectedError):
            LOGGER.warning("ProstoCAM refused a request: %s", err)
            return False
        return True

    @callback
    def _async_auth_failed(self) -> None:
        if self.auth_failed:
            return
        self.auth_failed = True
        LOGGER.warning("ProstoCAM rejected the token; pair Home Assistant again")
        self.entry.async_start_reauth(self.hass)

    @callback
    def _async_outdated(self) -> None:
        if self.outdated:
            return
        self.outdated = True
        LOGGER.warning("ProstoCAM needs a newer version of this integration")
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_OUTDATED,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_OUTDATED,
        )

    def _note_success(self) -> None:
        self.stats["last_success"] = dt_util.utcnow().isoformat()

    def _note_error(self, err: ProstoCamError) -> None:
        self.stats["last_error"] = f"{type(err).__name__}: {err}"
        self.stats["last_error_at"] = dt_util.utcnow().isoformat()

    def _data_to_save(self) -> dict[str, Any]:
        return {"stream": self.stream, "seq": self.seq, "pending": list(self.queue)}

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    def diagnostics(self) -> dict[str, Any]:
        """State of the bridge for diagnostics (no secrets inside)."""
        return {
            "server": self.client.server,
            "stream": self.stream,
            "seq": self.seq,
            "queue": len(self.queue),
            "queue_head": list(self.queue)[:5],
            "enabled_entities": sorted(self.enabled) if self.enabled is not None else None,
            "catalog_domains": sorted(self.catalog_domains)
            if self.catalog_domains is not None
            else None,
            "event_domains": sorted(self.event_domains)
            if self.event_domains is not None
            else None,
            "catalog_size": self.catalog_size,
            "catalog_pending": self._catalog_pending,
            "heartbeat_interval": self.heartbeat_interval,
            "auth_failed": self.auth_failed,
            "outdated": self.outdated,
            "domains": sorted(self.domains),
            "device_classes": sorted(self.device_classes),
            "stats": dict(self.stats),
        }
