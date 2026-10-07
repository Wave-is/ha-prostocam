"""Fixtures for the ProstoCAM tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntryState
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.prostocam.const import (
    API_PREFIX,
    CONF_INTEGRATION_ID,
    CONF_SERVER,
    CONF_TOKEN,
    DEFAULT_SERVER,
    DOMAIN,
)

TOKEN = "test-token-0123456789abcdef"
BASE = f"{DEFAULT_SERVER}{API_PREFIX}"
ENABLED = ("binary_sensor.front_door", "alarm_control_panel.home")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load integrations from custom_components."""
    return


@pytest.fixture(autouse=True)
async def unload_entries(hass: Any) -> AsyncGenerator[None]:
    """Unload ProstoCAM entries after each test, so no timer lingers."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def config_entry(hass: Any) -> MockConfigEntry:
    """A paired ProstoCAM entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ProstoCAM",
        unique_id="42",
        data={CONF_SERVER: DEFAULT_SERVER, CONF_TOKEN: TOKEN, CONF_INTEGRATION_ID: "42"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_server(aioclient_mock: AiohttpClientMocker) -> Callable[..., None]:
    """Register server replies; `events`/`alarm`/`heartbeat` take mocker kwargs."""

    def _register(
        enabled: Iterable[str] = ENABLED,
        events: dict[str, Any] | None = None,
        alarm: dict[str, Any] | None = None,
        heartbeat: dict[str, Any] | None = None,
    ) -> None:
        reply = {"data": {"enabled_entities": list(enabled)}}
        aioclient_mock.post(f"{BASE}/heartbeat", **(heartbeat or {"json": reply}))
        aioclient_mock.post(f"{BASE}/entities", json=reply)
        aioclient_mock.post(f"{BASE}/events", **(events or {"json": reply}))
        aioclient_mock.post(f"{BASE}/alarm", **(alarm or {"json": reply}))

    return _register


def calls_to(aioclient_mock: AiohttpClientMocker, path: str) -> list[tuple]:
    """Requests made to one endpoint: (method, url, json, headers)."""
    return [call for call in aioclient_mock.mock_calls if str(call[1]).endswith(f"/{path}")]


def sent_events(aioclient_mock: AiohttpClientMocker, path: str = "events") -> list[dict]:
    """All events sent to `/events` (or `/alarm`), in order."""
    return [event for call in calls_to(aioclient_mock, path) for event in call[2]["events"]]
