"""The ProstoCAM arming as an alarm panel of Home Assistant (`arming:write`)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProstoCamConfigEntry
from .control import ProstoCamControl
from .entity import ProstoCamAccountEntity

PARALLEL_UPDATES = 0

STATES: dict[str, AlarmControlPanelState] = {
    "disarmed": AlarmControlPanelState.DISARMED,
    "armed_home": AlarmControlPanelState.ARMED_HOME,
    "armed_night": AlarmControlPanelState.ARMED_NIGHT,
    "armed_away": AlarmControlPanelState.ARMED_AWAY,
    "arming": AlarmControlPanelState.ARMING,
    "triggered": AlarmControlPanelState.TRIGGERED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """The panel exists only when the subscriber let Home Assistant change the arming."""
    cameras = entry.runtime_data.cameras
    control = cameras.control if cameras is not None else None
    if control is None or not control.arming_enabled:
        return
    async_add_entities([ProstoCamArmingPanel(control)])


class ProstoCamArmingPanel(ProstoCamAccountEntity, AlarmControlPanelEntity):
    """«Охорона ProstoCAM»: the same modes and the same readiness check as the web account."""

    _attr_translation_key = "arming"
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_NIGHT
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(self, control: ProstoCamControl) -> None:
        """Create the panel."""
        self._init_account(control, "arming")

    @property
    def available(self) -> bool:
        """Known once the arming was read."""
        return self.control.arming is not None and not self.control.bridge.auth_failed

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """The mode of ProstoCAM in the words of Home Assistant."""
        state = self.control.arming_state
        return STATES.get(state) if state is not None else None

    @property
    def changed_by(self) -> str | None:
        """Who changed the arming last (a channel of ProstoCAM: app, telegram, schedule …)."""
        arming = self.control.arming or {}
        value = arming.get("changed_by")
        return value if isinstance(value, str) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Revision, source of the last change and the countdown."""
        arming = self.control.arming or {}
        pending = arming.get("pending")
        return {
            "revision": arming.get("revision"),
            "source": arming.get("source"),
            "changed_at": arming.get("changed_at"),
            "pending_state": pending.get("state") if isinstance(pending, dict) else None,
            "pending_effective_at": pending.get("effective_at")
            if isinstance(pending, dict)
            else None,
        }

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm ProstoCAM."""
        await self.control.async_set_arming("disarmed")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """«Вдома»."""
        await self.control.async_set_arming("armed_home")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """«Ніч»."""
        await self.control.async_set_arming("armed_night")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """«Нікого немає»."""
        await self.control.async_set_arming("armed_away")
