"""«Медіа» of Home Assistant: events and the archive of ProstoCAM cameras (protocol 3).

ProstoCAM → camera → day → events (frame and clip) and → archive → day → hour.
Frames come through Home Assistant (`/api/prostocam/…/snapshot.jpg`, the token
of ProstoCAM never reaches the browser); a clip or an hour of the archive is a
one-time HLS address the server gives for each playback (never cached).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import ProstoCamError, ProstoCamRejectedError
from .const import (
    DEFAULT_TITLE,
    DOMAIN,
    EVENT_MAX_PAGES,
    EVENT_PAGE_LIMIT,
    LOGGER,
    MEDIA_DAYS,
    SCOPE_ARCHIVE,
    SCOPE_EVENTS,
)
from .words import alarm_label, word

if TYPE_CHECKING:
    from .cameras import ProstoCamCameras

HLS_MIME = "application/vnd.apple.mpegurl"


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """The media source of ProstoCAM."""
    return ProstoCamMediaSource(hass)


def _int(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class ProstoCamMediaSource(MediaSource):
    """Events and archive of the cameras of every ProstoCAM connection."""

    name = DEFAULT_TITLE

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the source."""
        super().__init__(DOMAIN)
        self.hass = hass

    # ---------------------------------------------------------------- helpers

    def _word(self, key: str) -> str:
        return word(self.hass.config.language, key)

    def _hubs(self) -> dict[str, ProstoCamCameras]:
        hubs: dict[str, ProstoCamCameras] = {}
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            bridge = getattr(entry, "runtime_data", None)
            hub = bridge.cameras if bridge is not None else None
            if hub is not None and hub.control_enabled and hub.has(SCOPE_EVENTS):
                hubs[entry.entry_id] = hub
        return hubs

    def _hub(self, entry_id: str) -> ProstoCamCameras:
        hub = self._hubs().get(entry_id)
        if hub is None:
            raise Unresolvable(f"ProstoCAM connection {entry_id} has no media")
        return hub

    def _camera(self, hub: ProstoCamCameras, raw: str) -> int:
        camera_id = _int(raw)
        if camera_id is None or camera_id not in hub.cameras:
            raise Unresolvable(f"ProstoCAM camera {raw} is not shared")
        return camera_id

    def _folder(
        self,
        identifier: str | None,
        title: str,
        children: list[BrowseMediaSource] | None = None,
        *,
        children_class: MediaClass = MediaClass.DIRECTORY,
        thumbnail: str | None = None,
    ) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=identifier,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=children_class,
            thumbnail=thumbnail,
        )

    def _day_title(self, day: date) -> str:
        if day == dt_util.now().date():
            return f"{self._word('today')}, {day.isoformat()}"
        return day.isoformat()

    def _days(self) -> list[date]:
        today = dt_util.now().date()
        return [today - timedelta(days=offset) for offset in range(MEDIA_DAYS)]

    # ----------------------------------------------------------------- browse

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse ProstoCAM → camera → events/archive."""
        identifier = item.identifier or ""
        parts = identifier.split("/") if identifier else []
        if not parts:
            return self._browse_root()
        hub = self._hub(parts[0])
        if len(parts) == 1:
            return self._browse_cameras(parts[0], parts[0], hub, title=hub.entry.title)
        camera_id = self._camera(hub, parts[1])
        base = f"{parts[0]}/{camera_id}"
        if len(parts) == 2:
            return self._browse_camera(base, hub, camera_id)
        if parts[2] == "day" and len(parts) == 4 and (day := _parse_day(parts[3])):
            return await self._browse_events(base, hub, camera_id, day)
        if parts[2] == "archive" and len(parts) == 3:
            return self._folder(
                f"{base}/archive",
                f"{hub.camera_name(camera_id)} · {self._word('archive')}",
                [
                    self._folder(f"{base}/archive/{day.isoformat()}", self._day_title(day))
                    for day in self._days()
                ],
            )
        if parts[2] == "archive" and len(parts) == 4 and (day := _parse_day(parts[3])):
            return self._browse_hours(base, hub, camera_id, day)
        if parts[2] == "exports" and len(parts) == 3:
            return self._browse_exports(base, hub, camera_id)
        raise Unresolvable(f"Unknown ProstoCAM media {identifier}")

    def _browse_root(self) -> BrowseMediaSource:
        hubs = self._hubs()
        if len(hubs) == 1:
            entry_id, hub = next(iter(hubs.items()))
            return self._browse_cameras(None, entry_id, hub, title=DEFAULT_TITLE)
        return self._folder(
            None,
            DEFAULT_TITLE,
            [
                self._folder(entry_id, hub.entry.title or DEFAULT_TITLE)
                for entry_id, hub in hubs.items()
            ],
        )

    def _browse_cameras(
        self,
        identifier: str | None,
        entry_id: str,
        hub: ProstoCamCameras,
        *,
        title: str,
    ) -> BrowseMediaSource:
        return self._folder(
            identifier,
            title or DEFAULT_TITLE,
            [
                self._folder(f"{entry_id}/{camera_id}", hub.camera_name(camera_id))
                for camera_id in sorted(hub.cameras, key=hub.camera_name)
            ],
        )

    def _browse_camera(
        self, base: str, hub: ProstoCamCameras, camera_id: int
    ) -> BrowseMediaSource:
        children = [
            self._folder(
                f"{base}/day/{day.isoformat()}",
                f"{self._word('events')} · {self._day_title(day)}",
                children_class=MediaClass.VIDEO,
            )
            for day in self._days()
        ]
        if hub.has_control(SCOPE_ARCHIVE):
            children.append(self._folder(f"{base}/archive", self._word("archive")))
        if hub.archive is not None and hub.archive_enabled and hub.archive.exports.get(camera_id):
            children.append(
                self._folder(
                    f"{base}/exports", self._word("exports"), children_class=MediaClass.VIDEO
                )
            )
        return self._folder(base, hub.camera_name(camera_id), children)

    async def _browse_events(
        self, base: str, hub: ProstoCamCameras, camera_id: int, day: date
    ) -> BrowseMediaSource:
        start = dt_util.start_of_local_day(day)
        end = min(start + timedelta(days=1), dt_util.now())
        items = await self._events(hub, camera_id, start, end)
        playable = hub.has_control(SCOPE_ARCHIVE)
        children: list[BrowseMediaSource] = []
        for event in items:
            event_id = event.get("id")
            if not isinstance(event_id, int):
                continue
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{base}/event/{event_id}",
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=self._event_title(event),
                    can_play=playable,
                    can_expand=False,
                    thumbnail=hub.snapshot_path(event_id)
                    if event.get("has_frame") is True
                    else None,
                )
            )
        return self._folder(
            f"{base}/day/{day.isoformat()}",
            f"{hub.camera_name(camera_id)} · {self._day_title(day)}",
            children,
            children_class=MediaClass.VIDEO,
        )

    def _event_title(self, event: dict[str, Any]) -> str:
        occurred = event.get("occurred_at")
        moment = dt_util.parse_datetime(occurred) if isinstance(occurred, str) else None
        clock = dt_util.as_local(moment).strftime("%H:%M:%S") if moment else "—"
        kind = event.get("verdict") or event.get("type") or "other"
        if event.get("test") is True:
            kind = "test"
        label = alarm_label(
            self.hass.config.language, str(kind), event.get("verdict_confidence")
        )
        return f"{clock} · {label}"

    async def _events(
        self, hub: ProstoCamCameras, camera_id: int, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, EVENT_MAX_PAGES + 1):
            try:
                data = await hub.client.async_call(
                    "get",
                    f"/cameras/{camera_id}/events",
                    params={
                        "from": str(int(start.timestamp())),
                        "to": str(int(end.timestamp())),
                        "page": str(page),
                        "limit": str(EVENT_PAGE_LIMIT),
                    },
                )
            except ProstoCamError as err:
                LOGGER.warning("ProstoCAM camera %s: events not read (%s)", camera_id, err)
                break
            page_items = data.get("items")
            if isinstance(page_items, list):
                items += [item for item in page_items if isinstance(item, dict)]
            if data.get("has_more") is not True:
                break
        # Newest first, as in the web account.
        items.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
        return items

    def _browse_hours(
        self, base: str, hub: ProstoCamCameras, camera_id: int, day: date
    ) -> BrowseMediaSource:
        start = dt_util.start_of_local_day(day)
        now = dt_util.now()
        children: list[BrowseMediaSource] = []
        for hour in range(24):
            begin = start + timedelta(hours=hour)
            if begin >= now:
                break
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{base}/archive/{day.isoformat()}/{hour:02d}",
                    media_class=MediaClass.VIDEO,
                    media_content_type=MediaType.VIDEO,
                    title=f"{hour:02d}:00–{(hour + 1) % 24:02d}:00",
                    can_play=True,
                    can_expand=False,
                )
            )
        children.reverse()
        return self._folder(
            f"{base}/archive/{day.isoformat()}",
            f"{hub.camera_name(camera_id)} · {self._word('archive')} · {self._day_title(day)}",
            children,
            children_class=MediaClass.VIDEO,
        )

    def _browse_exports(
        self, base: str, hub: ProstoCamCameras, camera_id: int
    ) -> BrowseMediaSource:
        jobs = hub.archive.exports.get(camera_id, []) if hub.archive is not None else []
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{base}/export/{job['job_id']}",
                media_class=MediaClass.VIDEO,
                media_content_type=MediaType.VIDEO,
                title=self._export_title(job),
                can_play=job.get("state") != "failed",
                can_expand=False,
            )
            for job in jobs
        ]
        return self._folder(
            f"{base}/exports",
            f"{hub.camera_name(camera_id)} · {self._word('exports')}",
            children,
            children_class=MediaClass.VIDEO,
        )

    def _export_title(self, job: dict[str, Any]) -> str:
        begin = job.get("from")
        moment = dt_util.parse_datetime(begin) if isinstance(begin, str) else None
        when = dt_util.as_local(moment).strftime("%Y-%m-%d %H:%M:%S") if moment else f"#{job['job_id']}"
        duration = job.get("duration_s")
        title = f"{when} · {duration} s" if isinstance(duration, int) else when
        if job.get("state") == "failed":
            title += f" · {self._word('export_failed')}"
        return title

    # ---------------------------------------------------------------- resolve

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """A fresh one-time HLS address of a clip or an hour of the archive."""
        parts = (item.identifier or "").split("/")
        if len(parts) < 4:
            raise Unresolvable(f"Not a ProstoCAM recording: {item.identifier}")
        hub = self._hub(parts[0])
        camera_id = self._camera(hub, parts[1])
        if not hub.has_control(SCOPE_ARCHIVE):
            raise Unresolvable("ProstoCAM: no access to recordings (archive:read)")
        if parts[2] == "event" and len(parts) == 4 and (event_id := _int(parts[3])):
            return await self._play(hub, f"/events/{event_id}/clip", None, "no_clip")
        if parts[2] == "export" and len(parts) == 4 and (job_id := _int(parts[3])):
            return await self._play_export(hub, camera_id, job_id)
        if (
            parts[2] == "archive"
            and len(parts) == 5
            and (day := _parse_day(parts[3])) is not None
            and (hour := _int(parts[4])) is not None
            and hour < 24
        ):
            begin = dt_util.start_of_local_day(day) + timedelta(hours=hour)
            end = min(begin + timedelta(hours=1), dt_util.now())
            return await self._play(
                hub,
                f"/cameras/{camera_id}/archive",
                {"from": str(int(begin.timestamp())), "to": str(int(end.timestamp()))},
                "no_record",
            )
        raise Unresolvable(f"Not a ProstoCAM recording: {item.identifier}")

    async def _play(
        self,
        hub: ProstoCamCameras,
        path: str,
        params: dict[str, str] | None,
        missing: str,
    ) -> PlayMedia:
        try:
            data = await hub.client.async_call("get", path, params=params)
        except ProstoCamRejectedError as err:
            raise Unresolvable(err.message or self._word(missing)) from err
        except ProstoCamError as err:
            raise Unresolvable(f"ProstoCAM: {err}") from err
        url = data.get("hls_url")
        ranges = data.get("ranges")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise Unresolvable(self._word(missing))
        if isinstance(ranges, list) and not ranges:
            raise Unresolvable(self._word(missing))
        return PlayMedia(url, HLS_MIME)

    async def _play_export(
        self, hub: ProstoCamCameras, camera_id: int, job_id: int
    ) -> PlayMedia:
        """The exported file: a fresh one-time link for every playback."""
        archive = hub.archive
        if archive is None or not archive.enabled:
            raise Unresolvable("ProstoCAM: exports need protocol 4 and archive:read")
        try:
            job = await archive.async_job(camera_id, job_id)
        except ProstoCamRejectedError as err:
            raise Unresolvable(err.message or self._word("export_failed")) from err
        except ProstoCamError as err:
            raise Unresolvable(f"ProstoCAM: {err}") from err
        state = job.get("state")
        if state == "failed":
            raise Unresolvable(self._word("export_failed"))
        url = archive.download_url(job) if state == "ready" else None
        if url is None:
            raise Unresolvable(self._word("export_pending"))
        content_type = job.get("content_type")
        return PlayMedia(
            url, content_type if isinstance(content_type, str) and content_type else "video/mp4"
        )
