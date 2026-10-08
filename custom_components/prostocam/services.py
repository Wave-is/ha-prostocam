"""Services of ProstoCAM: mute, test alarm, AI check, arming anyway, ask the archive, export a clip."""

from __future__ import annotations

from datetime import timedelta
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
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.util import dt as dt_util

from .archive import loaded_archives
from .const import (
    ARMING_STATES,
    ASK_DEFAULT_LIMIT,
    ASK_MAX_LENGTH,
    ASK_MAX_LIMIT,
    DOMAIN,
    EXPORT_MAX_DURATION,
    MAX_MUTE_MINUTES,
    SERVICE_ARM_ANYWAY,
    SERVICE_ASK_ARCHIVE,
    SERVICE_EXPORT_CLIP,
    SERVICE_MUTE,
    SERVICE_TEST_ALARM,
    SERVICE_VERIFY_AI,
)

if TYPE_CHECKING:
    from .archive import ProstoCamArchive
    from .control import ProstoCamControl

ATTR_DEVICE_ID = "device_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_MINUTES = "minutes"
ATTR_EVENT_ID = "event_id"
ATTR_SPEND_CREDIT = "spend_credit"
ATTR_STATE = "state"
ATTR_QUESTION = "question"
ATTR_LIMIT = "limit"
ATTR_CAMERA = "camera"
ATTR_START = "start"
ATTR_DURATION = "duration"
ATTR_WAIT = "wait"

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

ASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_QUESTION): vol.All(
            cv.string, vol.Strip, vol.Length(min=1, max=ASK_MAX_LENGTH)
        ),
        vol.Optional(ATTR_LIMIT, default=ASK_DEFAULT_LIMIT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=ASK_MAX_LIMIT)
        ),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)
EXPORT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CAMERA): cv.entity_id,
        vol.Required(ATTR_START): cv.datetime,
        vol.Required(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=EXPORT_MAX_DURATION)
        ),
        vol.Optional(ATTR_WAIT, default=True): cv.boolean,
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



def _archive_of_entry(hass: HomeAssistant, entry_id: str | None) -> ProstoCamArchive:
    archives = loaded_archives(hass)
    if entry_id is not None:
        if entry_id not in archives:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
        return archives[entry_id]
    if len(archives) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="choose_entry"
        )
    return next(iter(archives.values()))


def _camera_of_entity(hass: HomeAssistant, entity_id: str) -> tuple[ProstoCamArchive, int]:
    """The archive and the camera number of a camera entity of ProstoCAM."""
    entity = er.async_get(hass).async_get(entity_id)
    archives = loaded_archives(hass)
    if (
        entity is not None
        and entity.platform == DOMAIN
        and entity.domain == "camera"
        and entity.config_entry_id in archives
    ):
        archive = archives[entity.config_entry_id]
        prefix = f"{archive.hub.uid}_"
        suffix = "_camera"
        unique_id = entity.unique_id
        if unique_id.startswith(prefix) and unique_id.endswith(suffix):
            number = unique_id[len(prefix) : -len(suffix)]
            if number.isdigit() and int(number) in archive.hub.cameras:
                return archive, int(number)
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_a_camera_entity"
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

    async def _ask_archive(call: ServiceCall) -> ServiceResponse:
        archive = _archive_of_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        return await archive.async_ask(call.data[ATTR_QUESTION], call.data[ATTR_LIMIT])

    async def _export_clip(call: ServiceCall) -> ServiceResponse:
        archive, camera_id = _camera_of_entity(hass, call.data[ATTR_CAMERA])
        start = call.data[ATTR_START]
        if start.tzinfo is None:
            # A time without a zone is a time of Home Assistant.
            start = start.replace(tzinfo=dt_util.get_default_time_zone())
        duration = call.data[ATTR_DURATION]
        if start + timedelta(seconds=duration) > dt_util.now():
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="export_in_future"
            )
        result = await archive.async_export(
            camera_id, start, duration, wait=call.data[ATTR_WAIT]
        )
        return result if call.return_response else None

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_ASK_ARCHIVE,
        _ask_archive,
        schema=ASK_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_CLIP,
        _export_clip,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
