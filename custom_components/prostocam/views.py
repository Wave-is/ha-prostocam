"""The frame of an event through Home Assistant: the media browser and phone notifications.

The browser and the companion app can not send the token of ProstoCAM (and
must never see it): they ask Home Assistant, with their own login, and Home
Assistant asks ProstoCAM with the token. Frames of events do not change, so the
reply may be cached by the browser.
"""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, VIEW_EVENT_SNAPSHOT


class ProstoCamEventSnapshotView(HomeAssistantView):
    """`GET /api/prostocam/{entry_id}/events/{event_id}/snapshot.jpg` (login or signed path)."""

    url = VIEW_EVENT_SNAPSHOT
    name = "api:prostocam:event_snapshot"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Remember Home Assistant."""
        self.hass = hass

    async def get(
        self, request: web.Request, entry_id: str, event_id: str
    ) -> web.Response:
        """The saved frame of the event, or 404."""
        if not event_id.isdigit():
            raise web.HTTPNotFound
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise web.HTTPNotFound
        bridge = getattr(entry, "runtime_data", None)
        cameras = bridge.cameras if bridge is not None else None
        if cameras is None:
            raise web.HTTPNotFound
        frame = await cameras.async_event_image(int(event_id))
        if frame is None:
            raise web.HTTPNotFound
        content, content_type = frame
        return web.Response(
            body=content,
            content_type=content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )


@callback
def async_register_views(hass: HomeAssistant) -> None:
    """Register the views once (Home Assistant without `http` has none)."""
    http = getattr(hass, "http", None)
    if http is None:
        return
    http.register_view(ProstoCamEventSnapshotView(hass))
