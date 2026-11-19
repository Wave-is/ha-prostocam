"""Common part of the entities of a ProstoCAM camera."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .cameras import CameraState, ProstoCamCameras
from .control import ProstoCamControl


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
    def available(self) -> bool:
        """Unavailable while ProstoCAM does not answer (an outage is not a revoked token)."""
        return not self.hub.bridge.server_unreachable

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
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.hub.bridge.signal_availability, self._async_availability_changed
            )
        )

    @callback
    def _async_camera_updated(self) -> None:
        self.async_write_ha_state()

    @callback
    def _async_availability_changed(self) -> None:
        """The server stopped or started answering (every entity, the event one too)."""
        self.async_write_ha_state()


class ProstoCamAccountEntity(Entity):
    """An entity of the ProstoCAM account itself (arming, money); its device is the account."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def _init_account(self, control: ProstoCamControl, key: str) -> None:
        """Bind the entity to the account (called from __init__ of the platform)."""
        self.control = control
        self._attr_unique_id = control.unique_id(key)
        self._attr_device_info = control.device_info()

    @property
    def available(self) -> bool:
        """Unavailable while ProstoCAM does not answer (an outage is not a revoked token)."""
        return not self.control.bridge.server_unreachable

    async def async_added_to_hass(self) -> None:
        """Follow the updates of the arming and the account."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.control.signal_update, self._async_control_updated
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.control.bridge.signal_availability, self._async_availability_changed
            )
        )

    @callback
    def _async_control_updated(self) -> None:
        self.async_write_ha_state()

    @callback
    def _async_availability_changed(self) -> None:
        """The server stopped or started answering."""
        self.async_write_ha_state()
