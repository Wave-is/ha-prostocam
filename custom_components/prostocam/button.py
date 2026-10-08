"""Buttons of a ProstoCAM camera (`actions:write`): test alarm, AI check, deterrence."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProstoCamConfigEntry
from .cameras import ProstoCamCameras, async_add_camera_entities
from .control import ProstoCamControl
from .entity import ProstoCamCameraEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Buttons of every camera, with access to actions only."""
    hub = entry.runtime_data.cameras
    if hub is None or hub.control is None or not hub.camera_actions:
        return
    control = hub.control

    def _build(camera_id: int) -> list[ButtonEntity]:
        entities: list[ButtonEntity] = [
            ProstoCamTestAlarmButton(hub, control, camera_id),
            ProstoCamAiCheckButton(hub, control, camera_id),
        ]
        if hub.states.get(camera_id) is not None and hub.states[camera_id].deter_capable:
            entities.append(ProstoCamDeterButton(hub, control, camera_id))
        return entities

    async_add_camera_entities(hub, entry, _build, async_add_entities)


class _ProstoCamButton(ProstoCamCameraEntity, ButtonEntity):
    key: str

    def __init__(self, hub: ProstoCamCameras, control: ProstoCamControl, camera_id: int) -> None:
        """Create the button."""
        self._init_camera(hub, camera_id, self.key)
        self.control = control
        self._attr_translation_key = self.key


class ProstoCamTestAlarmButton(_ProstoCamButton):
    """«Перевірити тривогу»: a test alarm through every channel of the subscriber."""

    key = "test_alarm"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Send the test alarm."""
        await self.control.async_test_alarm(self.camera_id)


class ProstoCamAiCheckButton(_ProstoCamButton):
    """«Перевірити ШІ» of the last alarm; off by default, spends a credit only on a second press."""

    key = "ai_check"
    _attr_entity_registry_enabled_default = False

    async def async_press(self) -> None:
        """First press: the price; a second press within 30 s: the check."""
        await self.control.async_press_ai(self.camera_id)


class ProstoCamDeterButton(_ProstoCamButton):
    """«Відлякати»: the siren or light of the device, after a fresh alarm only."""

    key = "deter"

    async def async_press(self) -> None:
        """Deter."""
        await self.control.async_deter(self.camera_id)
