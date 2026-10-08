"""Protocol 4 of ProstoCAM: ask the archive, export a clip (contract §13.2–§14).

* The question goes to the same search as the search line of the web account
  (no model, no AI credits). The reply is a short phrase in the language of
  Home Assistant for Assist and the events with their frames (through Home
  Assistant, never an address with a token) and clips (Media).
* An export is the same job as "Export" of the web account: ordered, then read
  every 5 s until the file is ready (5 minutes at most). The download link is
  short-lived and new on every read; Media lists the exports of each camera and
  takes a fresh link for every playback.

Neither the text of a question nor a download link reaches the log or diagnostics.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    intent,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import const
from .api import ProstoCamAuthError, ProstoCamError, ProstoCamRejectedError, ProstoCamScopeError
from .const import (
    ASK_DEFAULT_LIMIT,
    DOMAIN,
    EVENT_EXPORT_READY,
    EXPORTS_KEPT,
    INTENT_ASK_ARCHIVE,
    LOGGER,
    PATH_ASK,
    SCOPE_ARCHIVE,
    STORAGE_VERSION,
)
from .errors import NO_RECORDING_CODES, command_error, export_error, recorded_ranges
from .words import events_noun, word

if TYPE_CHECKING:
    from .cameras import ProstoCamCameras

KINDS = ("person", "vehicle", "animal")
PENDING_STATES = frozenset({"queued", "running", "processing", "pending"})
EXPORT_SAVE_DELAY = 5  # seconds


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def export_media_id(entry_id: str, camera_id: int, job_id: int) -> str:
    """Media source id of an exported clip."""
    return f"media-source://{DOMAIN}/{entry_id}/{camera_id}/export/{job_id}"


def event_media_id(entry_id: str, camera_id: int, event_id: int) -> str:
    """Media source id of the clip of an event."""
    return f"media-source://{DOMAIN}/{entry_id}/{camera_id}/event/{event_id}"


class ProstoCamArchive:
    """The question to the archive and the export of clips of one entry."""

    def __init__(self, hass: HomeAssistant, hub: ProstoCamCameras) -> None:
        """Prepare; the list of exports is read in `async_start`."""
        self.hass = hass
        self.hub = hub
        self.entry = hub.entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{hub.entry.entry_id}.exports"
        )
        # Camera → exports, newest first: job_id, from, to, duration_s, state.
        self.exports: dict[int, list[dict[str, Any]]] = {}
        self.stats: dict[str, Any] = {
            "questions": 0,
            "question_errors": 0,
            "exports": 0,
            "exports_ready": 0,
            "exports_failed": 0,
            "export_errors": 0,
            "error_codes": {},
        }

    @property
    def enabled(self) -> bool:
        """The server speaks protocol 4 and the token may read recordings."""
        return self.hub.archive_enabled

    async def async_start(self) -> None:
        """Read the exports listed in Media."""
        stored = await self._store.async_load() or {}
        exports = stored.get("exports")
        if not isinstance(exports, dict):
            return
        for raw_camera, jobs in exports.items():
            camera_id = _int(raw_camera)
            if camera_id is None or not isinstance(jobs, list):
                continue
            self.exports[camera_id] = [
                job for job in jobs if isinstance(job, dict) and _int(job.get("job_id"))
            ][:EXPORTS_KEPT]

    async def async_stop(self) -> None:
        """Keep the list of exports."""
        await self._store.async_save(self._data_to_save())

    def _data_to_save(self) -> dict[str, Any]:
        return {"exports": {str(camera): jobs for camera, jobs in self.exports.items()}}

    def require(self) -> None:
        """Refuse an action the server or the access does not allow."""
        if not self.hub.v4_enabled:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="needs_protocol_4"
            )
        if not self.enabled:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_access",
                translation_placeholders={"scope": SCOPE_ARCHIVE},
            )

    def _failed(self, err: ProstoCamError) -> None:
        if isinstance(err, ProstoCamScopeError):
            self.hub.deny(SCOPE_ARCHIVE, err)
        elif isinstance(err, ProstoCamAuthError):
            self.hub.bridge.report_auth_failed()

    # ------------------------------------------------------------- question

    async def async_ask(self, question: str, limit: int = ASK_DEFAULT_LIMIT) -> dict[str, Any]:
        """Ask the archive in plain words; the answer for Assist and the events."""
        self.require()
        try:
            data = await self.hub.client.async_call(
                "post", PATH_ASK, {"question": question, "page": 1, "limit": limit}
            )
        except ProstoCamError as err:
            self.stats["question_errors"] += 1
            self._failed(err)
            raise command_error(err, self.stats["error_codes"]) from err
        self.stats["questions"] += 1
        raw_items = data.get("items")
        items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        events = [self._event(item) for item in items]
        understood = data.get("fully_understood") is not False
        unsupported = [
            {"code": _str(part.get("code")), "text": _str(part.get("text"))}
            for part in data.get("unsupported") or []
            if isinstance(part, dict)
        ]
        has_more = data.get("has_more") is True
        return {
            "answer": self._answer(events, has_more, understood, unsupported),
            "from": data.get("from"),
            "to": data.get("to"),
            "fully_understood": understood,
            "unsupported": unsupported,
            "has_more": has_more,
            "events": events,
        }

    def _event(self, item: dict[str, Any]) -> dict[str, Any]:
        raw_camera = _int(item.get("camera_id"))
        camera_id = self.hub.camera_for(raw_camera) if raw_camera is not None else None
        event_id = _int(item.get("id"))
        links = item.get("links") if isinstance(item.get("links"), dict) else {}
        verdict = _str(item.get("verdict"))
        kind = self._kind(item)
        entity_id = None
        if camera_id is not None:
            entity_id = er.async_get(self.hass).async_get_entity_id(
                "camera", DOMAIN, self.hub.unique_id(camera_id, "camera")
            )
        has_frame = item.get("has_frame") is True or links.get("snapshot") is not None
        return {
            "id": event_id,
            "camera": _str(item.get("camera_name"))
            or (self.hub.camera_name(camera_id) if camera_id is not None else None),
            "camera_id": camera_id,
            "entity_id": entity_id,
            "at": item.get("occurred_at"),
            "type": item.get("type"),
            "verdict": verdict,
            "confidence": item.get("verdict_confidence"),
            "kind": kind,
            "test": item.get("test") is True,
            "snapshot_url": self.hub.snapshot_path(event_id)
            if event_id is not None and has_frame
            else None,
            "clip": event_media_id(self.entry.entry_id, camera_id, event_id)
            if event_id is not None and camera_id is not None and links.get("clip")
            else None,
        }

    @staticmethod
    def _kind(item: dict[str, Any]) -> str:
        if item.get("test") is True:
            return "test"
        verdict = item.get("verdict")
        if verdict in KINDS:
            return str(verdict)
        return "motion" if item.get("type") == "motion" else "other"

    def _answer(
        self,
        events: list[dict[str, Any]],
        has_more: bool,
        understood: bool,
        unsupported: list[dict[str, Any]],
    ) -> str:
        """«Знайшов 3 події, остання о 14:05 — Вхід, людина» in the language of HA."""
        language = self.hass.config.language
        parts: list[str] = []
        moments = [
            (moment, event)
            for event in events
            if isinstance(event["at"], str)
            and (moment := dt_util.parse_datetime(event["at"])) is not None
        ]
        if moments:
            moment, last = max(moments, key=lambda pair: pair[0])
            local = dt_util.as_local(moment)
            clock = (
                local.strftime("%H:%M")
                if local.date() == dt_util.now().date()
                else local.strftime("%d.%m %H:%M")
            )
            count = len(events)
            key = "ask_more" if has_more else "ask_one" if count == 1 else "ask_many"
            parts.append(
                word(language, key).format(
                    count=count,
                    noun=events_noun(language, count),
                    time=clock,
                    camera=last["camera"] or "—",
                    kind=word(language, last["kind"]).lower(),
                )
            )
        elif events:
            parts.append(word(language, "ask_many").split(",")[0].format(
                count=len(events), noun=events_noun(language, len(events))
            ) + ".")
        elif understood or not unsupported:
            parts.append(word(language, "ask_nothing"))
        if not understood:
            words = ", ".join(
                part["text"] or part["code"] or "?" for part in unsupported
            ) or "?"
            parts.append(word(language, "ask_unclear").format(words=words))
        return " ".join(parts)

    # --------------------------------------------------------------- export

    async def async_export(
        self, camera_id: int, start: datetime, duration: int, *, wait: bool = True
    ) -> dict[str, Any]:
        """Order a clip of the archive; with `wait` — until the file is ready."""
        self.require()
        body = {"from": start.isoformat(timespec="seconds"), "duration_s": duration}
        try:
            job = await self.hub.client.async_call(
                "post", f"/cameras/{camera_id}/exports", body
            )
        except ProstoCamError as err:
            self.stats["export_errors"] += 1
            self._failed(err)
            raise command_error(err, self.stats["error_codes"]) from err
        self.stats["exports"] += 1
        job_id = _int(job.get("job_id"))
        if job_id is None:
            self.stats["export_errors"] += 1
            raise export_error("no_job", self.stats["error_codes"])
        self._remember(camera_id, job_id, job, duration)
        if wait:
            job = await self._async_wait(camera_id, job_id, job)
        if _str(job.get("state")) == "failed":
            self.stats["exports_failed"] += 1
            raise await self._export_failure(camera_id, job)
        return self._result(camera_id, job_id, job)

    async def _export_failure(
        self, camera_id: int, job: dict[str, Any]
    ) -> ServiceValidationError:
        """The reason of a failed export; with no recording — the nearest recording."""
        code = _str(job.get("error_code"))
        nearest = None
        if code in NO_RECORDING_CODES:
            nearest = await self._nearest_recording(camera_id, job)
        return export_error(code, self.stats["error_codes"], nearest)

    async def _nearest_recording(self, camera_id: int, job: dict[str, Any]) -> str | None:
        """«07.10 14:20:04–14:21:16, 14:30:04–14:30:44»: the recordings around the clip.

        The archive answers `404 no_recording` for a window without a recorded
        second, with the nearest recorded pieces before and after it (contract §11.3).
        """
        begin = dt_util.parse_datetime(str(job.get("from") or ""))
        end = dt_util.parse_datetime(str(job.get("to") or ""))
        if begin is None or end is None or end <= begin:
            return None
        try:
            await self.hub.client.async_call(
                "get",
                f"/cameras/{camera_id}/archive",
                params={
                    "from": str(int(begin.timestamp())),
                    "to": str(int(end.timestamp())),
                },
            )
        except ProstoCamRejectedError as err:
            ranges = recorded_ranges(err.detail) if err.code == "no_recording" else []
        except ProstoCamError as err:
            LOGGER.debug("ProstoCAM nearest recording not read (%s)", err)
            return None
        else:
            return None  # the window has a recording by now: nothing to point at
        if not ranges:
            return None
        today = dt_util.now().date()
        parts = []
        for start, duration in ranges:
            first = dt_util.as_local(dt_util.utc_from_timestamp(start))
            last = dt_util.as_local(dt_util.utc_from_timestamp(start + duration))
            day = "" if first.date() == today else first.strftime("%d.%m ")
            parts.append(f"{day}{first:%H:%M:%S}–{last:%H:%M:%S}")
        return ", ".join(parts)

    async def _async_wait(
        self, camera_id: int, job_id: int, job: dict[str, Any]
    ) -> dict[str, Any]:
        """Read the job every 5 s until it is ready or failed (5 minutes at most)."""
        for _ in range(max(1, const.EXPORT_WAIT // max(1, const.EXPORT_POLL))):
            if not self._pending(job):
                return job
            await asyncio.sleep(const.EXPORT_POLL)
            try:
                job = await self.async_job(camera_id, job_id)
            except ProstoCamRejectedError as err:
                raise command_error(err, self.stats["error_codes"]) from err
            except ProstoCamError as err:
                # A lost answer: the job goes on at the server, read it again.
                LOGGER.debug("ProstoCAM export %s not read (%s)", job_id, err)
                if isinstance(err, ProstoCamAuthError | ProstoCamScopeError):
                    self._failed(err)
                    raise command_error(err, self.stats["error_codes"]) from err
        return job

    async def async_job(self, camera_id: int, job_id: int) -> dict[str, Any]:
        """The state of an export; a ready one carries a fresh download link."""
        job = await self.hub.client.async_call(
            "get", f"/cameras/{camera_id}/exports/{job_id}"
        )
        self._remember(camera_id, job_id, job)
        return job

    @staticmethod
    def _pending(job: dict[str, Any]) -> bool:
        state = job.get("state")
        if state in ("ready", "failed"):
            return False
        return job.get("pending") is True or state in PENDING_STATES

    def download_url(self, job: dict[str, Any]) -> str | None:
        """The full address of the file (short-lived, one-time)."""
        url = _str(job.get("download_url"))
        if url is None:
            return None
        if url.startswith(("https://", "http://")):
            return url
        if url.startswith("/"):
            return f"{self.hub.client.server}{url}"
        return None

    def _result(self, camera_id: int, job_id: int, job: dict[str, Any]) -> dict[str, Any]:
        state = _str(job.get("state"))
        url = self.download_url(job) if state == "ready" else None
        device = self.hub.device_of(camera_id)
        result: dict[str, Any] = {
            "job_id": job_id,
            "camera_id": camera_id,
            "camera": self.hub.camera_name(camera_id),
            "device_id": device.id if device is not None else None,
            "state": state,
            "pending": self._pending(job),
            "progress_percent": job.get("progress_percent"),
            "from": job.get("from"),
            "to": job.get("to"),
            "url": url,
            "expires_at": job.get("download_expires_at"),
            "content_type": job.get("content_type"),
            "size_bytes": job.get("size_bytes"),
            "sha256": job.get("sha256"),
            "media_content_id": export_media_id(self.entry.entry_id, camera_id, job_id),
        }
        if url is not None:
            self.stats["exports_ready"] += 1
            self.hass.bus.async_fire(
                EVENT_EXPORT_READY, {"entry_id": self.entry.entry_id, **result}
            )
        return result

    def _remember(
        self,
        camera_id: int,
        job_id: int,
        job: dict[str, Any],
        duration: int | None = None,
    ) -> None:
        """Keep the job for Media (never its link)."""
        jobs = self.exports.setdefault(camera_id, [])
        known = next((item for item in jobs if item.get("job_id") == job_id), None)
        if known is None:
            known = {"job_id": job_id, "duration_s": duration}
            jobs.insert(0, known)
            del jobs[EXPORTS_KEPT:]
        for key in ("from", "to", "state", "content_type", "size_bytes", "sha256"):
            if job.get(key) is not None:
                known[key] = job[key]
        self._store.async_delay_save(self._data_to_save, EXPORT_SAVE_DELAY)

    def diagnostics(self) -> dict[str, Any]:
        """Counts only: no questions, no links."""
        return {
            "enabled": self.enabled,
            "exports": {
                str(camera): [
                    {key: job.get(key) for key in ("job_id", "state", "duration_s")}
                    for job in jobs
                ]
                for camera, jobs in self.exports.items()
            },
            "stats": dict(self.stats),
        }


# ------------------------------------------------------------------ Assist


def loaded_archives(hass: HomeAssistant) -> dict[str, ProstoCamArchive]:
    """The archives of every loaded ProstoCAM connection."""
    archives: dict[str, ProstoCamArchive] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        bridge = getattr(entry, "runtime_data", None)
        cameras = bridge.cameras if bridge is not None else None
        if cameras is not None and cameras.archive is not None:
            archives[entry.entry_id] = cameras.archive
    return archives


class AskArchiveIntentHandler(intent.IntentHandler):
    """«Знайди в архіві …» for Assist: the same search as the action `ask_archive`."""

    intent_type = INTENT_ASK_ARCHIVE
    description = (
        "Searches the video archive of the ProstoCAM cameras for events that match "
        "a question in plain words (who came to the gate today, cars yesterday "
        "evening) and says what was found and when."
    )
    slot_schema = {vol.Required("question"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Ask the archive of the (first) connection that may read recordings."""
        slots = self.async_validate_slots(intent_obj.slots)
        question = str(slots["question"]["value"]).strip()[: const.ASK_MAX_LENGTH]
        archives = [
            archive
            for archive in loaded_archives(intent_obj.hass).values()
            if archive.enabled
        ]
        if not archives or not question:
            raise intent.IntentHandleError(
                "ProstoCAM: no connection may read the archive (archive:read)"
            )
        try:
            result = await archives[0].async_ask(question)
        except HomeAssistantError as err:
            raise intent.IntentHandleError(str(err) or "ProstoCAM") from err
        response = intent_obj.create_response()
        response.async_set_speech(result["answer"])
        return response
