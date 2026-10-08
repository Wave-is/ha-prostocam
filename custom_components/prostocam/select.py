"""«Не турбувати» of a ProstoCAM camera (`actions:write`): mute its alarms for a while."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ProstoCamConfigEntry
from .cameras import ProstoCamCameras, async_add_camera_entities
from .const import MAX_MUTE_MINUTES, MORNING_HOUR
from .control import ProstoCamControl
from .entity import ProstoCamCameraEntity

PARALLEL_UPDATES = 1

OPTION_OFF = "off"
OPTION_HOUR = "1h"
OPTION_MORNING = "until_morning"
# Muted for another period (the web account, the service `prostocam.mute`).
OPTION_MUTED = "muted"
OPTIONS = [OPTION_OFF, OPTION_HOUR, OPTION_MORNING, OPTION_MUTED]
HOUR_MINUTES = 60


def minutes_until_morning(now: datetime) -> int:
    """Minutes from `now` (local time) to the next 07:00, within 1…1440."""
    morning = now.replace(hour=MORNING_HOUR, minute=0, second=0, microsecond=0)
    if morning <= now:
        morning += timedelta(days=1)
    minutes = int((morning - now).total_seconds() // 60)
    return max(1, min(MAX_MUTE_MINUTES, minutes))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """«Не турбувати» of every camera, with access to actions only."""
    hub = entry.runtime_data.cameras
    if hub is None or hub.control is None or not hub.camera_actions:
        return
    control = hub.control
    async_add_camera_entities(
        hub,
        entry,
        lambda camera_id: [ProstoCamDoNotDisturb(hub, control, camera_id)],
        async_add_entities,
    )


class ProstoCamDoNotDisturb(ProstoCamCameraEntity, SelectEntity):
    """Off, for an hour, until the morning; alarms are still written to the feed."""

    _attr_translation_key = "do_not_disturb"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = OPTIONS

    def __init__(self, hub: ProstoCamCameras, control: ProstoCamControl, camera_id: int) -> None:
        """Create the select."""
        self._init_camera(hub, camera_id, "do_not_disturb")
        self.control = control

    @property
    def available(self) -> bool:
        """Known once the mute of the camera was read."""
        return self.camera_state.muted is not None

    @property
    def current_option(self) -> str | None:
        """What the camera is muted for now."""
        state = self.camera_state
        if state.muted is None:
            return None
        if not state.muted:
            return OPTION_OFF
        if state.mute_choice in (OPTION_HOUR, OPTION_MORNING):
            return state.mute_choice
        return OPTION_MUTED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Until when the alarms of the camera are muted."""
        return {"muted_until": self.camera_state.muted_until}

    async def async_select_option(self, option: str) -> None:
        """Mute or unmute the alarms of the camera."""
        if option == OPTION_OFF:
            minutes = 0
        elif option == OPTION_MORNING:
            minutes = minutes_until_morning(dt_util.now())
        else:
            minutes = HOUR_MINUTES
        await self.control.async_set_mute(self.camera_id, minutes, option)
