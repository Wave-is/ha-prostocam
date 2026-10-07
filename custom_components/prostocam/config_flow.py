"""Config flow for ProstoCAM: a pairing code from the web account, nothing else."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    ProstoCamAuthError,
    ProstoCamClient,
    ProstoCamConnectionError,
    ProstoCamOutdatedError,
    ProstoCamRateLimitedError,
    ProstoCamRejectedError,
)
from .const import (
    CODE_LENGTH,
    CONF_CODE,
    CONF_DEVICE_CLASSES,
    CONF_DOMAINS,
    CONF_INTEGRATION_ID,
    CONF_SERVER,
    CONF_TOKEN,
    DEFAULT_DEVICE_CLASSES,
    DEFAULT_DOMAINS,
    DEFAULT_SERVER,
    DEFAULT_TITLE,
    DOMAIN,
    ERROR_ALREADY_CONNECTED,
    ERROR_CLIENT_OUTDATED,
    KEY_DISPLAY_NAME,
    KEY_INTEGRATION_ID,
    KEY_TOKEN,
    LOGGER,
    SUPPORTED_DEVICE_CLASSES,
    SUPPORTED_DOMAINS,
)

KEY_TITLE = "title"
CODE_RE = re.compile(rf"^[A-Z0-9]{{{CODE_LENGTH}}}$")


def normalize_code(raw: str | None) -> str:
    """`abcd-1234`, `ABCD 1234` and `abcd1234` are the same code."""
    return re.sub(r"[\s\-]", "", raw or "").upper()


def normalize_server(raw: str | None) -> str | None:
    """Return the server base address, or None when it is not a web address."""
    value = (raw or "").strip().rstrip("/")
    try:
        url = URL(value)
    except ValueError:
        return None
    if url.scheme not in ("https", "http") or not url.host:
        return None
    return value


class ProstoCamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair Home Assistant with a ProstoCAM account."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the pairing code (and the server in advanced mode)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            server = normalize_server(user_input.get(CONF_SERVER, DEFAULT_SERVER))
            if server is None:
                errors[CONF_SERVER] = "invalid_server"
            else:
                result = await self._async_pair(user_input.get(CONF_CODE), server, errors)
                if result is not None:
                    await self.async_set_unique_id(result[CONF_INTEGRATION_ID])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=result[KEY_TITLE],
                        data={
                            CONF_SERVER: server,
                            CONF_TOKEN: result[CONF_TOKEN],
                            CONF_INTEGRATION_ID: result[CONF_INTEGRATION_ID],
                        },
                    )

        fields: dict[Any, Any] = {vol.Required(CONF_CODE): str}
        if self.show_advanced_options:
            fields[vol.Required(CONF_SERVER, default=DEFAULT_SERVER)] = str
        schema = vol.Schema(fields)
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(
                schema, {k: v for k, v in user_input.items() if k != CONF_CODE}
            )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The server rejected the token: ask for a new code."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again with a new code from the web account."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._async_pair(
                user_input.get(CONF_CODE), entry.data[CONF_SERVER], errors
            )
            if result is not None:
                await self.async_set_unique_id(result[CONF_INTEGRATION_ID])
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_TOKEN: result[CONF_TOKEN],
                        CONF_INTEGRATION_ID: result[CONF_INTEGRATION_ID],
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
        )

    async def _async_pair(
        self, raw_code: str | None, server: str, errors: dict[str, str]
    ) -> dict[str, str] | None:
        """Exchange the code for a token; fill `errors` on failure."""
        code = normalize_code(raw_code)
        if not CODE_RE.fullmatch(code):
            errors[CONF_CODE] = "invalid_code"
            return None
        client = ProstoCamClient(async_get_clientsession(self.hass), server)
        try:
            data = await client.async_pair(code, HA_VERSION)
        except ProstoCamOutdatedError:
            errors["base"] = "client_outdated"
            return None
        except ProstoCamRateLimitedError:
            errors["base"] = "too_many_attempts"
            return None
        except ProstoCamConnectionError:
            errors["base"] = "cannot_connect"
            return None
        except ProstoCamRejectedError as err:
            if err.code == ERROR_ALREADY_CONNECTED:
                errors["base"] = "already_connected"
            elif err.code == ERROR_CLIENT_OUTDATED:
                errors["base"] = "client_outdated"
            else:
                errors[CONF_CODE] = "invalid_code"
            return None
        except ProstoCamAuthError:
            errors[CONF_CODE] = "invalid_code"
            return None
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error while pairing with ProstoCAM")
            errors["base"] = "unknown"
            return None
        token = data.get(KEY_TOKEN)
        integration_id = data.get(KEY_INTEGRATION_ID)
        if not isinstance(token, str) or not token or integration_id is None:
            LOGGER.error("ProstoCAM replied to pairing without a token")
            errors["base"] = "unknown"
            return None
        title = data.get(KEY_DISPLAY_NAME)
        return {
            CONF_TOKEN: token,
            CONF_INTEGRATION_ID: str(integration_id),
            KEY_TITLE: f"{DEFAULT_TITLE}: {title}"
            if isinstance(title, str) and title and title != DEFAULT_TITLE
            else DEFAULT_TITLE,
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Choose what Home Assistant shares with ProstoCAM."""
        return ProstoCamOptionsFlow()


class ProstoCamOptionsFlow(OptionsFlow):
    """Which domains and binary sensor classes are shared."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the filter."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_DOMAINS):
                errors[CONF_DOMAINS] = "no_domains"
            else:
                return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DOMAINS,
                    default=list(options.get(CONF_DOMAINS, DEFAULT_DOMAINS)),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=SUPPORTED_DOMAINS,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_DOMAINS,
                    )
                ),
                vol.Required(
                    CONF_DEVICE_CLASSES,
                    default=list(
                        options.get(CONF_DEVICE_CLASSES, DEFAULT_DEVICE_CLASSES)
                    ),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=SUPPORTED_DEVICE_CLASSES,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_DEVICE_CLASSES,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
