"""Config flow.

Bluetooth-discovery first: HA's bluetooth integration surfaces MOBIUS devices
automatically (via the `bluetooth` block in manifest.json). User clicks
Configure and the entry is created against that specific MAC.

Also supports manual entry as a fallback for cases where discovery is flaky.

Options flow (PR-7): an ".aip" profile importer. HA has no native Lovelace
file-upload card, so uploading a schedule profile is done here via HA's
FileSelector (Settings -> Devices & Services -> AI Prime -> Configure, or the
"Upload .aip profile" button on the control subpage). The uploaded file is
validated as a real .aip and written to <config>/aiprime/profiles/. Importing
updates the entry options, which triggers a reload so the new profile shows up
in the schedule select.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_NAME, DOMAIN, PROFILES_SUBDIR
from .protocol.aip import AipParseError, save_profile_file

_LOGGER = logging.getLogger(__name__)

_CONF_PROFILE_FILE = "profile_file"
_CONF_PROFILE_NAME = "name"


class AIPrimeBLEConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AI Prime BLE."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "AIPrimeBLEOptionsFlow":
        """Options flow — currently just the .aip profile importer."""
        return AIPrimeBLEOptionsFlow()

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


class AIPrimeBLEOptionsFlow(config_entries.OptionsFlow):
    """Options flow: upload an .aip schedule profile.

    `self.config_entry` is provided by the framework — do not set it here.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_import_profile()

    async def async_step_import_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Upload + validate + store an .aip into <config>/aiprime/profiles/."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            file_id = user_input[_CONF_PROFILE_FILE]
            name = user_input.get(_CONF_PROFILE_NAME)
            try:
                saved = await self.hass.async_add_executor_job(
                    self._save_uploaded, file_id, name
                )
            except AipParseError as err:
                _LOGGER.warning("Rejected .aip upload: %s", err)
                errors["base"] = "invalid_aip"
            except OSError as err:
                _LOGGER.error("Failed to store uploaded .aip: %s", err)
                errors["base"] = "write_failed"
            else:
                # Bump options so the update listener reloads the entry and the
                # schedule select re-scans the profiles directory.
                new_options = {
                    **dict(self.config_entry.options),
                    "last_imported_profile": saved,
                }
                return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                vol.Required(_CONF_PROFILE_FILE): selector.FileSelector(
                    selector.FileSelectorConfig(accept=".aip,text/xml,application/xml")
                ),
                vol.Optional(_CONF_PROFILE_NAME): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="import_profile",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    def _save_uploaded(self, file_id: str, name: str | None) -> str:
        """Blocking: pull the uploaded file and write it to the profiles dir."""
        dest_dir = self.hass.config.path(PROFILES_SUBDIR)
        with process_uploaded_file(self.hass, file_id) as src_path:
            return save_profile_file(src_path, name, dest_dir)
