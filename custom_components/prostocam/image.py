"""The last alarm frame of a ProstoCAM camera."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProstoCamConfigEntry
from .cameras import ProstoCamCameras, async_add_camera_entities
from .const import SCOPE_CAMERAS, SCOPE_EVENTS
from .entity import ProstoCamCameraEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A last alarm frame per camera, with access to events only."""
    hub = entry.runtime_data.cameras
    if hub is None or not hub.has(SCOPE_CAMERAS) or not hub.has(SCOPE_EVENTS):
        return
    async_add_camera_entities(
        hub,
        entry,
        lambda camera_id: [ProstoCamAlarmImage(hass, hub, camera_id)],
        async_add_entities,
    )


class ProstoCamAlarmImage(ProstoCamCameraEntity, ImageEntity):
    """The frame of the camera taken when its last alarm arrived."""

    _attr_translation_key = "last_alarm"

    def __init__(self, hass: HomeAssistant, hub: ProstoCamCameras, camera_id: int) -> None:
        """Create the image entity."""
        ImageEntity.__init__(self, hass)
        self._init_camera(hub, camera_id, "last_alarm")

    @property
    def image_last_updated(self) -> datetime | None:
        """When the frame of the last alarm was taken."""
        return self.camera_state.alarm_image_at

    @property
    def content_type(self) -> str:
        """Type of the stored frame."""
        return self.camera_state.alarm_image_type

    async def async_image(self) -> bytes | None:
        """The stored frame of the last alarm."""
        return self.camera_state.alarm_image
