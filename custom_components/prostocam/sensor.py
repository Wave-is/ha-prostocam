"""Money of the ProstoCAM account (`account:read`): balance, tariff, AI credits, next charge."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ProstoCamConfigEntry
from .control import ACCOUNT_SENSORS, ProstoCamControl
from .entity import ProstoCamAccountEntity

PARALLEL_UPDATES = 0


def _minor(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value / 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProstoCamConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Money sensors, with access to the account only."""
    cameras = entry.runtime_data.cameras
    control = cameras.control if cameras is not None else None
    if control is None or not control.account_enabled:
        return
    async_add_entities([ProstoCamAccountSensor(control, key) for key in ACCOUNT_SENSORS])


class ProstoCamAccountSensor(ProstoCamAccountEntity, SensorEntity):
    """One number or word of `GET /v2/smart-home/ha/account`."""

    def __init__(self, control: ProstoCamControl, key: str) -> None:
        """Create the sensor."""
        self._init_account(control, f"account_{key}")
        self.key = key
        self._attr_translation_key = key
        if key == "ai_credits":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key == "next_charge":
            self._attr_device_class = SensorDeviceClass.DATE

    @property
    def _account(self) -> dict[str, Any]:
        return self.control.account or {}

    @property
    def available(self) -> bool:
        """Known once the account was read."""
        return self.control.account is not None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """The balance is money only when the currency is known."""
        if self.key == "balance":
            currency = self._account.get("currency")
            return SensorDeviceClass.MONETARY if isinstance(currency, str) and currency else None
        return super().device_class

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The currency of the balance."""
        if self.key == "balance":
            currency = self._account.get("currency")
            return currency if isinstance(currency, str) and currency else None
        return None

    @property
    def native_value(self) -> str | int | float | date | None:
        """The value from the last read."""
        account = self._account
        if self.key == "balance":
            return _minor(account.get("balance_minor"))
        if self.key == "tariff":
            tariff = account.get("tariff")
            return tariff if isinstance(tariff, str) else None
        if self.key == "ai_credits":
            credits = account.get("ai_credits")
            return credits if isinstance(credits, int) and not isinstance(credits, bool) else None
        if self.key == "next_charge":
            raw = account.get("next_charge_date")
            return dt_util.parse_date(raw) if isinstance(raw, str) else None
        stage = account.get("stage")
        if isinstance(stage, str):
            return stage
        return "not_connected" if account.get("connected") is False else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Details that belong to the value."""
        account = self._account
        if self.key == "ai_credits":
            return {"included": account.get("ai_credits_included")}
        if self.key == "next_charge":
            return {
                "next_fee": _minor(account.get("next_fee_minor")),
                "currency": account.get("currency"),
                "paid_until": account.get("paid_until"),
            }
        return None
