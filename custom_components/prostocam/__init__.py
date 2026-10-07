"""ProstoCAM: share security sensors of Home Assistant with ProstoCAM cameras."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ProstoCamAuthError, ProstoCamClient
from .bridge import ProstoCamBridge
from .const import CONF_SERVER, CONF_TOKEN

type ProstoCamConfigEntry = ConfigEntry[ProstoCamBridge]


async def async_setup_entry(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> bool:
    """Start the bridge for a paired ProstoCAM account."""
    client = ProstoCamClient(
        async_get_clientsession(hass), entry.data[CONF_SERVER], entry.data[CONF_TOKEN]
    )
    bridge = ProstoCamBridge(hass, entry, client)
    try:
        await bridge.async_start()
    except ProstoCamAuthError as err:
        await bridge.async_stop()
        raise ConfigEntryAuthFailed("ProstoCAM rejected the token") from err
    entry.runtime_data = bridge
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> bool:
    """Stop the bridge; unsent events stay stored for the next start."""
    await entry.runtime_data.async_stop()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ProstoCamConfigEntry) -> None:
    """Options changed: restart with the new filter."""
    await hass.config_entries.async_reload(entry.entry_id)
