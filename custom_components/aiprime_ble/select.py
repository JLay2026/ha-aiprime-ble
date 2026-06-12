"""Select entity for choosing which .aip schedule profile to deploy.

Options are discovered from <config>/aiprime/profiles/*.aip at entity add.
Selecting an option records the choice in the per-entry data dict; the Deploy
button (or the aiprime_ble.deploy_profile service) pushes it to the device.
The chosen value is restored across restarts.

NOTE: new .aip files are picked up on the next integration reload (options are
scanned once at setup). The deploy service accepts an arbitrary profile name.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .schedule_deploy import async_list_profiles


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AIPrimeActiveProfileSelect(entry_data)])


class AIPrimeActiveProfileSelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:playlist-music"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry_data: dict[str, Any]) -> None:
        self._entry_data = entry_data
        self._hub = entry_data["hub"]
        self._attr_unique_id = f"{self._hub.address}-active_profile_select"
        self._attr_name = "Schedule profile"
        self._attr_options: list[str] = []
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._hub.address)},
            name=self._hub.name,
        )

    @property
    def available(self) -> bool:
        return self._hub.state.ble_connected

    @property
    def current_option(self) -> str | None:
        selected = self._entry_data.get("selected_profile")
        if selected and selected in self._attr_options:
            return selected
        return None

    async def async_added_to_hass(self) -> None:
        self._attr_options = await async_list_profiles(self.hass)

        last = await self.async_get_last_state()
        if (
            self._entry_data.get("selected_profile") is None
            and last is not None
            and last.state in self._attr_options
        ):
            self._entry_data["selected_profile"] = last.state

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

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            self._attr_options = await async_list_profiles(self.hass)
        self._entry_data["selected_profile"] = option
        self.async_write_ha_state()
