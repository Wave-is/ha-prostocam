"""Saved connections leave the retired name new.prosto.cam (1.0.3, config entry 1.2)."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.prostocam import async_migrate_entry, migrated_server
from custom_components.prostocam.const import (
    CONF_INTEGRATION_ID,
    CONF_SERVER,
    CONF_TOKEN,
    DEFAULT_SERVER,
    DOMAIN,
)

from .conftest import TOKEN


def _entry(hass: HomeAssistant, server: str, *, version: int = 1, minor: int = 1) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ProstoCAM",
        unique_id="42",
        version=version,
        minor_version=minor,
        data={CONF_SERVER: server, CONF_TOKEN: TOKEN, CONF_INTEGRATION_ID: "42"},
    )
    entry.add_to_hass(hass)
    return entry


def test_default_server_is_fyi() -> None:
    """New connections pair with my.prostocam.fyi."""
    assert DEFAULT_SERVER == "https://my.prostocam.fyi"


@pytest.mark.parametrize(
    "server",
    [
        "https://new.prosto.cam",
        "https://new.prosto.cam/",
        "http://new.prosto.cam",
        "new.prosto.cam",
        "new.prosto.cam/",
        " HTTPS://NEW.Prosto.Cam/ ",
        "https://new.prosto.cam:443",
    ],
)
async def test_old_server_moves_to_fyi(hass: HomeAssistant, server: str) -> None:
    """Any spelling of new.prosto.cam becomes my.prostocam.fyi; the token stays."""
    entry = _entry(hass, server)
    with patch("custom_components.prostocam.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_SERVER] == "https://my.prostocam.fyi"
    assert entry.data[CONF_TOKEN] == TOKEN
    assert entry.data[CONF_INTEGRATION_ID] == "42"
    assert (entry.version, entry.minor_version) == (1, 2)


@pytest.mark.parametrize(
    "server",
    [
        "https://cam.example.org",
        "https://cam.example.org/",
        "https://prosto.cam",
        "https://new.prosto.cam.example.org",
        "https://new.prosto.cam/own-path",
        "https://my.prostocam.fyi",
    ],
)
async def test_other_server_is_kept(hass: HomeAssistant, server: str) -> None:
    """A server of the subscriber's own is not touched, only the entry version moves."""
    entry = _entry(hass, server)
    with patch("custom_components.prostocam.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_SERVER] == server
    assert entry.data[CONF_TOKEN] == TOKEN
    assert (entry.version, entry.minor_version) == (1, 2)


async def test_migration_twice_is_safe(hass: HomeAssistant) -> None:
    """A second run changes nothing: an entry of 1.2 is left as it is."""
    entry = _entry(hass, "https://new.prosto.cam/")
    assert await async_migrate_entry(hass, entry)
    first = dict(entry.data)
    assert first[CONF_SERVER] == DEFAULT_SERVER

    assert await async_migrate_entry(hass, entry)
    assert dict(entry.data) == first
    assert (entry.version, entry.minor_version) == (1, 2)


async def test_current_entry_is_not_rewritten(hass: HomeAssistant) -> None:
    """An entry already at 1.2 keeps even a server on the old name (set by hand after the move)."""
    entry = _entry(hass, "https://new.prosto.cam", minor=2)
    assert await async_migrate_entry(hass, entry)
    assert entry.data[CONF_SERVER] == "https://new.prosto.cam"


async def test_newer_entry_is_refused(hass: HomeAssistant) -> None:
    """An entry written by a newer major version is not loaded by this one."""
    entry = _entry(hass, "https://new.prosto.cam", version=2)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.data[CONF_SERVER] == "https://new.prosto.cam"


@pytest.mark.parametrize("server", [None, "", "   ", "https://[bad"])
def test_unreadable_server_is_kept(server: str | None) -> None:
    """Nothing readable as an address: no new address is offered."""
    assert migrated_server(server) is None
