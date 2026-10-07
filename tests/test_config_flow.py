"""Config and options flow of ProstoCAM."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.prostocam.const import (
    CONF_CODE,
    CONF_DEVICE_CLASSES,
    CONF_DOMAINS,
    CONF_INTEGRATION_ID,
    CONF_SERVER,
    CONF_TOKEN,
    DEFAULT_SERVER,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BASE, TOKEN, server_config

PAIR_URL = f"{BASE}/pair"
NEW_TOKEN = "new-token-fedcba9876543210"


def _pair_reply(integration_id: int = 42) -> dict:
    return {
        "data": {
            "token": NEW_TOKEN,
            "integration_id": integration_id,
            "renewed": False,
            "config": server_config(),
            "display_name": "Dacha",
        }
    }


async def test_user_flow_pairs_with_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A code from the web account becomes a token."""
    aioclient_mock.post(PAIR_URL, json=_pair_reply())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # The advanced mode is gone from Home Assistant: the server field is always there
    # with its default, a code alone is enough.
    assert CONF_SERVER in result["data_schema"].schema

    with patch("custom_components.prostocam.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: " abcd-1234 "}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ProstoCAM: Dacha"
    assert result["data"] == {
        CONF_SERVER: DEFAULT_SERVER,
        CONF_TOKEN: NEW_TOKEN,
        CONF_INTEGRATION_ID: "42",
    }
    assert result["result"].unique_id == "42"
    assert len(aioclient_mock.mock_calls) == 1
    sent = aioclient_mock.mock_calls[0][2]
    assert sent["code"] == "ABCD1234"
    assert set(sent) == {"code", "client_version", "ha_version"}
    assert "Authorization" not in (aioclient_mock.mock_calls[0][3] or {})


async def test_user_flow_advanced_server(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Advanced mode lets the subscriber change the server."""
    aioclient_mock.post("https://cam.example.org/v2/smart-home/ha/pair", json=_pair_reply())
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER, "show_advanced_options": True},
    )
    assert CONF_SERVER in result["data_schema"].schema

    with patch("custom_components.prostocam.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CODE: "ABCD1234", CONF_SERVER: "https://cam.example.org/"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERVER] == "https://cam.example.org"


async def test_user_flow_bad_server(hass: HomeAssistant) -> None:
    """A server that is not a web address is refused before any request."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER, "show_advanced_options": True},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234", CONF_SERVER: "new.prosto.cam"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SERVER: "invalid_server"}


async def test_user_flow_malformed_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A code of the wrong shape never reaches the server."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "12-34"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CODE: "invalid_code"}
    assert aioclient_mock.call_count == 0


async def test_user_flow_rejected_code(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unknown or expired code is reported on the code field, then retried."""
    aioclient_mock.post(
        PAIR_URL, status=404, json={"error": {"code": "pairing_code_invalid"}}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CODE: "invalid_code"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(PAIR_URL, json=_pair_reply())
    with patch("custom_components.prostocam.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: "WXYZ9876"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Network trouble is a form error, not a crash."""
    aioclient_mock.post(PAIR_URL, exc=aiohttp.ClientConnectionError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_server_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A server error is reported as cannot_connect."""
    aioclient_mock.post(PAIR_URL, status=502)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The same ProstoCAM connection is not added twice."""
    aioclient_mock.post(PAIR_URL, json=_pair_reply(42))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_replaces_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A new code from Reconnect gives the same connection a new token."""
    aioclient_mock.post(PAIR_URL, json=_pair_reply(42))
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch("custom_components.prostocam.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CODE: "ABCD1234"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == NEW_TOKEN
    assert config_entry.data[CONF_TOKEN] != TOKEN


async def test_reauth_wrong_account(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """A code of another connection does not overwrite this one."""
    aioclient_mock.post(PAIR_URL, json=_pair_reply(77))
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data[CONF_TOKEN] == TOKEN


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """The subscriber chooses domains and binary sensor classes."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DOMAINS: [], CONF_DEVICE_CLASSES: ["door"]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DOMAINS: "no_domains"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DOMAINS: ["binary_sensor", "siren"], CONF_DEVICE_CLASSES: ["door"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_DOMAINS: ["binary_sensor", "siren"],
        CONF_DEVICE_CLASSES: ["door"],
    }


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        ({"status": 409, "json": {"message": "x", "code": "already_connected"}}, "already_connected"),
        ({"status": 426, "json": {"message": "x", "code": "client_outdated"}}, "client_outdated"),
        ({"status": 429, "json": {"message": "x", "code": "rate_limited"}}, "too_many_attempts"),
        ({"status": 404, "json": {"message": "x", "code": "not_found"}}, "not_available"),
    ],
)
async def test_user_flow_server_refusals(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    reply: dict,
    error: str,
) -> None:
    """Refusals the subscriber can act on get their own message."""
    aioclient_mock.post(PAIR_URL, **reply)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "ABCD1234"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
