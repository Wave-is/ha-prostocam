"""Diagnostics for ProstoCAM; the token never leaves Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ProstoCamConfigEntry
from .const import CONF_TOKEN

TO_REDACT = {CONF_TOKEN}


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
    }
