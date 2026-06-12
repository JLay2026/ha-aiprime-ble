"""Button that deploys the currently-selected .aip schedule profile.

Pressing it runs the 3-frame attr-500 deploy for the profile chosen via the
Schedule profile select entity. For automations, prefer the
aiprime_ble.deploy_profile service which takes the profile name directly.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .schedule_deploy import async_deploy_profile


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AIPrimeDeployProfileButton(entry_data)])


class AIPrimeDeployProfileButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:upload"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry_data: dict[str, Any]) -> None:
        self._entry_data = entry_data
        self._hub = entry_data["hub"]
        self._attr_unique_id = f"{self._hub.address}-deploy_profile_button"
        self._attr_name = "Deploy schedule profile"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._hub.address)},
            name=self._hub.name,
        )

    @property
    def available(self) -> bool:
        return self._hub.state.ble_connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._hub.signal_state_updated,
                self._handle_state_changed,
            )
        )

    @callback
    def _handle_state_changed(self) -> None:
        self.async_write_ha_state()

    async def async_press(self) -> None:
        name = self._entry_data.get("selected_profile")
        if not name:
            raise HomeAssistantError(
                "No schedule profile selected — pick one with the "
                "'Schedule profile' select first."
            )
        ok = await async_deploy_profile(self.hass, self._entry_data, name)
        if not ok:
            raise HomeAssistantError(f"Failed to deploy schedule profile '{name}'")
