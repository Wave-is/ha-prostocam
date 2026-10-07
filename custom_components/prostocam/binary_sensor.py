"""Binary sensors of a ProstoCAM camera: motion, person, vehicle, connection."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProstoCamConfigEntry
from .cameras import ProstoCamCameras, async_add_camera_entities
from .const import DETECTIONS, SCOPE_CAMERAS, SCOPE_EVENTS
from .entity import ProstoCamCameraEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Connection of every camera; detections only with access to events."""
    hub = entry.runtime_data.cameras
    if hub is None or not hub.has(SCOPE_CAMERAS):
        return
    events = hub.has(SCOPE_EVENTS)

    def _build(camera_id: int) -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = [ProstoCamConnectivity(hub, camera_id)]
        if events:
            entities += [ProstoCamDetection(hub, camera_id, kind) for kind in DETECTIONS]
        return entities

    async_add_camera_entities(hub, entry, _build, async_add_entities)


class ProstoCamDetection(ProstoCamCameraEntity, BinarySensorEntity):
    """On for 30 s after an alarm of the camera of this kind."""

    def __init__(self, hub: ProstoCamCameras, camera_id: int, kind: str) -> None:
        """Create a detection sensor (`motion`, `person` or `vehicle`)."""
        self._init_camera(hub, camera_id, kind)
        self.kind = kind
        if kind == "motion":
            self._attr_device_class = BinarySensorDeviceClass.MOTION
        else:
            self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
            self._attr_translation_key = kind

    @property
    def is_on(self) -> bool:
        """A detection of this kind in the last 30 s."""
        return self.camera_state.detections.get(self.kind, False)


class ProstoCamConnectivity(ProstoCamCameraEntity, BinarySensorEntity):
    """The camera is online for ProstoCAM."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: ProstoCamCameras, camera_id: int) -> None:
        """Create the connectivity sensor."""
        self._init_camera(hub, camera_id, "connectivity")

    @property
    def is_on(self) -> bool | None:
        """Online, offline or not known yet."""
        return self.camera_state.online

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | None]:
        """Video state of the camera as ProstoCAM sees it."""
        state = self.camera_state
        return {
            "video_state": state.video_state,
            "streaming": state.streaming,
            "last_frame_at": state.last_frame_at,
        }
