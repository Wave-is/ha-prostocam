"""Diagnostics for ProstoCAM; the token never leaves Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ProstoCamConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN}
# Never in camera diagnostics either (a safety net: they are not collected).
CAMERA_REDACT = {CONF_TOKEN, "hls_url", "live_key", "stream_url_template", "links", "explanation"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ProstoCamConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    bridge = getattr(entry, "runtime_data", None)
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "bridge": bridge.diagnostics() if bridge is not None else None,
        "cameras": async_redact_data(bridge.cameras.diagnostics(), CAMERA_REDACT)
        if bridge is not None and bridge.cameras is not None
        else None,
    }
