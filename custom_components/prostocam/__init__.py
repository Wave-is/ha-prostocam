"""ProstoCAM: security sensors of Home Assistant to ProstoCAM, cameras of ProstoCAM to Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv, device_registry as dr, intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ProstoCamAuthError, ProstoCamClient
from .archive import AskArchiveIntentHandler, ProstoCamArchive
from .bridge import ProstoCamBridge
from .cameras import ProstoCamCameras
from .const import CONF_SERVER, CONF_TOKEN, DOMAIN, PLATFORMS
from .control import ProstoCamControl
from .services import async_setup_services
from .views import async_register_views

type ProstoCamConfigEntry = ConfigEntry[ProstoCamBridge]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services and the frame proxy exist once, for every connection."""
    async_setup_services(hass)
    async_register_views(hass)
    intent.async_register(hass, AskArchiveIntentHandler())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> bool:
    """Start the bridge for a paired ProstoCAM account, then its cameras and controls."""
    client = ProstoCamClient(
        async_get_clientsession(hass),
        entry.data[CONF_SERVER],
        entry.data[CONF_TOKEN],
        language=lambda: hass.config.language,
    )
    bridge = ProstoCamBridge(hass, entry, client)
    try:
        await bridge.async_start()
    except ProstoCamAuthError as err:
        await bridge.async_stop()
        raise ConfigEntryAuthFailed("ProstoCAM rejected the token") from err
    cameras = ProstoCamCameras(hass, entry, client, bridge)
    bridge.cameras = cameras
    control = ProstoCamControl(hass, entry, client, bridge, cameras)
    cameras.control = control
    archive = ProstoCamArchive(hass, cameras)
    cameras.archive = archive
    entry.runtime_data = bridge
    await cameras.async_start()
    await control.async_start()
    await archive.async_start()
    if cameras.platforms_needed:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        cameras.platforms_loaded = True
    cameras.async_remove_stale_entities()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> bool:
    """Stop the bridge; unsent events stay stored for the next start."""
    bridge = entry.runtime_data
    cameras = bridge.cameras
    unloaded = True
    if cameras is not None and cameras.platforms_loaded:
        unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    if cameras is not None:
        if cameras.control is not None:
            await cameras.control.async_stop()
        if cameras.archive is not None:
            await cameras.archive.async_stop()
        await cameras.async_stop()
    await bridge.async_stop()
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ProstoCamConfigEntry, device: dr.DeviceEntry
) -> bool:
    """A camera device may be removed by hand once ProstoCAM no longer shares it."""
    cameras = getattr(entry, "runtime_data", None)
    cameras = cameras.cameras if cameras is not None else None
    if cameras is None:
        return True
    shared = {
        cameras.device_identifier(camera_id) for camera_id in cameras.cameras
    }
    if cameras.control is not None and cameras.control.expected_unique_ids():
        shared.add(cameras.control.device_identifier)
    return not any(
        domain == DOMAIN and identifier in shared
        for domain, identifier in device.identifiers
    )


async def _async_update_listener(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> None:
    """Options changed: restart with the new filter."""
    await hass.config_entries.async_reload(entry.entry_id)
