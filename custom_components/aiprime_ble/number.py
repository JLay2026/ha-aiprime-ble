"""Per-channel Number entities — one per LED channel.

Values are exposed to users as 0-100 (percent) and converted to the device's
0-1000 per-mille scale inside the hub.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .aiprime_hub import AIPrimeHub, get_hub
from .const import DOMAIN, device_to_percent, percent_to_device
from .entity_builders import ChannelEntityDescriptor, build_led_channel_descriptors


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = get_hub(hass, entry)
    entities = [
        AIPrimeChannelNumber(hub, desc)
        for desc in build_led_channel_descriptors()
    ]
    async_add_entities(entities)


class AIPrimeChannelNumber(NumberEntity):
    """A single LED channel exposed as a 0-100 slider."""

    _attr_has_entity_name = True
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, hub: AIPrimeHub, descriptor: ChannelEntityDescriptor
    ) -> None:
        self._hub = hub
        self._desc = descriptor
        self._attr_unique_id = f"{hub.address}-{descriptor.unique_id_suffix}"
        self._attr_name = descriptor.label
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.address)},
            name=hub.name,
        )

    @property
    def available(self) -> bool:
        return self._hub.state.ble_connected

    @property
    def native_value(self) -> float | None:
        ch = self._hub.state.channel(self._desc.channel_id)
        if ch is None:
            return None
        return device_to_percent(ch.value_device)

    async def async_set_native_value(self, value: float) -> None:
        device_value = percent_to_device(value)
        await self._hub.async_set_channel(self._desc.channel_id, device_value)

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
