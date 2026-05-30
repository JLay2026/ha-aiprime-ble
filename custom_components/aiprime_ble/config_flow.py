"""Config flow.

Bluetooth-discovery first: HA's bluetooth integration surfaces MOBIUS devices
automatically (via the `bluetooth` block in manifest.json). User clicks
Configure and the entry is created against that specific MAC.

Also supports manual entry as a fallback for cases where discovery is flaky.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_NAME, DOMAIN


class AIPrimeBLEConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AI Prime BLE."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None

    # --- Discovery via HA bluetooth integration --------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a BT advertisement matching our manifest filter."""
        address = format_mac(discovery_info.address)
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self._discovered_address = discovery_info.address
        self._discovered_name = discovery_info.name or "AI Prime"

        # Show in HA's "Discovered" section
        self.context["title_placeholders"] = {
            "name": self._discovered_name,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm pairing with the discovered device."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_name or "AI Prime",
                data={
                    CONF_ADDRESS: self._discovered_address,
                    CONF_NAME: self._discovered_name,
                },
            )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovered_name or "AI Prime",
                "address": self._discovered_address or "",
            },
        )

    # --- Manual entry (fallback) -----------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup — user enters the BLE MAC explicitly."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = format_mac(user_input[CONF_ADDRESS])
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=user_input.get(CONF_NAME) or "AI Prime",
                data={
                    CONF_ADDRESS: user_input[CONF_ADDRESS],
                    CONF_NAME: user_input.get(CONF_NAME) or "AI Prime",
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default="AI Prime"): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
