"""Aggregate Light entity — one per device.

Brightness is computed as the max of LED channel values; on/off toggles
all LED channels at once. Per-channel control happens via the Number
entities in number.py.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .aiprime_hub import AIPrimeHub, get_hub
from .const import (
    DEVICE_VALUE_MAX,
    DOMAIN,
    USER_VALUE_MAX,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = get_hub(hass, entry)
    async_add_entities([AIPrimeAggregateLight(hub)])


class AIPrimeAggregateLight(LightEntity):
    """One LightEntity that represents the whole fixture (on/off + brightness)."""

    _attr_has_entity_name = True
    _attr_name = None  # use device name as the entity name
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, hub: AIPrimeHub) -> None:
        self._hub = hub
        self._attr_unique_id = f"{hub.address}-light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.address)},
            name=hub.name,
            manufacturer="Aqua Illumination",
            model="Prime HD",
            connections={("bluetooth", hub.address)} if hub.address else set(),
        )

    @property
    def is_on(self) -> bool:
        return self._hub.is_on()

    @property
    def brightness(self) -> int | None:
        """HA wants 0-255; we hold 0-1000 internally."""
        device = self._hub.aggregate_brightness_device()
        if device <= 0:
            return 0
        return round(device * 255 / DEVICE_VALUE_MAX)

    @property
    def available(self) -> bool:
        return self._hub.state.ble_connected

    async def async_turn_on(self, **kwargs: Any) -> None:
        # TODO Day 4: honor ATTR_BRIGHTNESS by scaling all channels
        # proportionally to the requested brightness.
        await self._hub.async_set_power(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._hub.async_set_power(on=False)

    async def async_added_to_hass(self) -> None:
        """Subscribe to hub state-change signals."""
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
