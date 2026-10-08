"""Services of ProstoCAM: mute, test alarm, AI check, arming despite not ready cameras."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    ARMING_STATES,
    DOMAIN,
    MAX_MUTE_MINUTES,
    SERVICE_ARM_ANYWAY,
    SERVICE_MUTE,
    SERVICE_TEST_ALARM,
    SERVICE_VERIFY_AI,
)

if TYPE_CHECKING:
    from .control import ProstoCamControl

ATTR_DEVICE_ID = "device_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_MINUTES = "minutes"
ATTR_EVENT_ID = "event_id"
ATTR_SPEND_CREDIT = "spend_credit"
ATTR_STATE = "state"

MUTE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_MUTE_MINUTES)
        ),
    }
)
TEST_ALARM_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
VERIFY_AI_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_EVENT_ID): cv.positive_int,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        # Required on purpose: spending a credit is never a default.
        vol.Required(ATTR_SPEND_CREDIT): cv.boolean,
    }
)
ARM_ANYWAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_STATE): vol.In([s for s in ARMING_STATES if s != "disarmed"]),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


def _loaded_controls(hass: HomeAssistant) -> dict[str, ProstoCamControl]:
    controls: dict[str, ProstoCamControl] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        bridge = getattr(entry, "runtime_data", None)
        cameras = bridge.cameras if bridge is not None else None
        if cameras is not None and cameras.control is not None:
            controls[entry.entry_id] = cameras.control
    return controls


def _control_of_entry(hass: HomeAssistant, entry_id: str | None) -> ProstoCamControl:
    controls = _loaded_controls(hass)
    if entry_id is not None:
        if entry_id not in controls:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
        return controls[entry_id]
    if len(controls) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="choose_entry"
        )
    return next(iter(controls.values()))


def _camera_of_device(hass: HomeAssistant, device_id: str) -> tuple[ProstoCamControl, int]:
    device = dr.async_get(hass).async_get(device_id)
    controls = _loaded_controls(hass)
    if device is not None:
        for entry_id in device.config_entries:
            control = controls.get(entry_id)
            if control is None:
                continue
            prefix = f"{control.cameras.uid}_camera_"
            for domain, identifier in device.identifiers:
                if domain == DOMAIN and identifier.startswith(prefix):
                    suffix = identifier[len(prefix) :]
                    if suffix.isdigit() and int(suffix) in control.cameras.cameras:
                        return control, int(suffix)
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_a_camera")


def _require_actions(control: ProstoCamControl) -> None:
    if not control.cameras.camera_actions:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_access",
            translation_placeholders={"scope": "actions:write"},
        )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of the integration (once, for every connection)."""

    async def _mute(call: ServiceCall) -> None:
        control, camera_id = _camera_of_device(hass, call.data[ATTR_DEVICE_ID])
        _require_actions(control)
        await control.async_set_mute(camera_id, call.data[ATTR_MINUTES], "custom")

    async def _test_alarm(call: ServiceCall) -> None:
        control, camera_id = _camera_of_device(hass, call.data[ATTR_DEVICE_ID])
        _require_actions(control)
        await control.async_test_alarm(camera_id)

    async def _verify_ai(call: ServiceCall) -> ServiceResponse:
        camera_id: int | None = None
        if ATTR_DEVICE_ID in call.data:
            control, camera_id = _camera_of_device(hass, call.data[ATTR_DEVICE_ID])
        elif ATTR_EVENT_ID in call.data:
            control = _control_of_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        else:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_event"
            )
        _require_actions(control)
        result = await control.async_verify_ai(
            camera_id,
            call.data.get(ATTR_EVENT_ID),
            spend_credit=call.data[ATTR_SPEND_CREDIT],
        )
        if not call.return_response:
            return None
        allowed = (
            "status",
            "event_id",
            "camera_id",
            "classification",
            "confidence",
            "reason",
            "words",
            "phrase",
            "frames",
            "queue_wait_seconds",
            "series_wait_seconds",
        )
        response: dict[str, Any] = {key: result.get(key) for key in allowed if key in result}
        return response

    async def _arm_anyway(call: ServiceCall) -> None:
        control = _control_of_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        if not control.arming_enabled:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_access",
                translation_placeholders={"scope": "arming:write"},
            )
        await control.async_arm_anyway(call.data[ATTR_STATE])

    hass.services.async_register(DOMAIN, SERVICE_MUTE, _mute, schema=MUTE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_TEST_ALARM, _test_alarm, schema=TEST_ALARM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_VERIFY_AI,
        _verify_ai,
        schema=VERIFY_AI_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ARM_ANYWAY, _arm_anyway, schema=ARM_ANYWAY_SCHEMA
    )
