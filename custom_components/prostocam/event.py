"""The alarm event of a ProstoCAM camera, with the verdict of the AI."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProstoCamConfigEntry
from .cameras import ProstoCamCameras, async_add_camera_entities
from .const import EVENT_TYPES, SCOPE_CAMERAS, SCOPE_EVENTS
from .entity import ProstoCamCameraEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """An alarm event entity per camera, with access to events only."""
    hub = entry.runtime_data.cameras
    if hub is None or not hub.has(SCOPE_CAMERAS) or not hub.has(SCOPE_EVENTS):
        return
    async_add_camera_entities(
        hub, entry, lambda camera_id: [ProstoCamAlarmEvent(hub, camera_id)], async_add_entities
    )


class ProstoCamAlarmEvent(ProstoCamCameraEntity, EventEntity):
    """Fires on every alarm of the camera: motion, person, vehicle, animal, other, test."""

    _attr_device_class = EventDeviceClass.MOTION
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "alarm"

    def __init__(self, hub: ProstoCamCameras, camera_id: int) -> None:
        """Create the alarm event entity."""
        self._init_camera(hub, camera_id, "alarm")

    async def async_added_to_hass(self) -> None:
        """Listen to the alarms of the camera."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.hub.signal_alarm(self.camera_id), self._async_alarm
            )
        )

    @callback
    def _async_camera_updated(self) -> None:
        """The state of an event entity changes only with an event."""

    @callback
    def _async_alarm(self, event_type: str, attributes: dict[str, Any]) -> None:
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
