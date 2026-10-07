"""ProstoCAM: security sensors of Home Assistant to ProstoCAM, cameras of ProstoCAM to Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ProstoCamAuthError, ProstoCamClient
from .bridge import ProstoCamBridge
from .cameras import ProstoCamCameras
from .const import CONF_SERVER, CONF_TOKEN, DOMAIN, PLATFORMS

type ProstoCamConfigEntry = ConfigEntry[ProstoCamBridge]


async def async_setup_entry(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> bool:
    """Start the bridge for a paired ProstoCAM account, then its cameras."""
    client = ProstoCamClient(
        async_get_clientsession(hass), entry.data[CONF_SERVER], entry.data[CONF_TOKEN]
    )
    bridge = ProstoCamBridge(hass, entry, client)
    try:
        await bridge.async_start()
    except ProstoCamAuthError as err:
        await bridge.async_stop()
        raise ConfigEntryAuthFailed("ProstoCAM rejected the token") from err
    cameras = ProstoCamCameras(hass, entry, client, bridge)
    bridge.cameras = cameras
    entry.runtime_data = bridge
    await cameras.async_start()
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
    return not any(
        domain == DOMAIN and identifier in shared
        for domain, identifier in device.identifiers
    )


async def _async_update_listener(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> None:
    """Options changed: restart with the new filter."""
    await hass.config_entries.async_reload(entry.entry_id)
