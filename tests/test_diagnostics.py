"""Diagnostics never contain the token."""

from __future__ import annotations

from collections.abc import Callable
import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.prostocam.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant

from .conftest import TOKEN


async def test_diagnostics_redact_token(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_server: Callable[..., None],
) -> None:
    """The token is redacted; the bridge state is there."""
    mock_server()
    hass.states.async_set("binary_sensor.front_door", "off", {"device_class": "door"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert TOKEN not in json.dumps(result)
    assert result["entry"]["data"]["token"] == "**REDACTED**"
    assert result["bridge"]["catalog_size"] == 1
    assert result["bridge"]["queue"] == 0
    assert result["bridge"]["enabled_entities"] == [
        "alarm_control_panel.home",
        "binary_sensor.front_door",
    ]
