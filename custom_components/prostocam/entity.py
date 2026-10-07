"""Common part of the entities of a ProstoCAM camera."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .cameras import CameraState, ProstoCamCameras


class ProstoCamCameraEntity(Entity):
    """An entity of one camera; its device is the camera."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def _init_camera(self, hub: ProstoCamCameras, camera_id: int, key: str) -> None:
        """Bind the entity to a camera (called from __init__ of the platform)."""
        self.hub = hub
        self.camera_id = camera_id
        self._attr_unique_id = hub.unique_id(camera_id, key)
        self._attr_device_info = hub.device_info(camera_id)

    @property
    def camera_state(self) -> CameraState:
        """What is known about the camera now."""
        return self.hub.states.setdefault(self.camera_id, CameraState())

    async def async_added_to_hass(self) -> None:
        """Follow the updates of the camera."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.hub.signal_update(self.camera_id), self._async_camera_updated
            )
        )

    @callback
    def _async_camera_updated(self) -> None:
        self.async_write_ha_state()
