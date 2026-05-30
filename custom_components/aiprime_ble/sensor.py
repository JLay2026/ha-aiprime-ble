"""Diagnostic sensors — RSSI, firmware, fan."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .aiprime_hub import AIPrimeHub, get_hub
from .const import CHANNEL_ID_FAN, DOMAIN, device_to_percent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = get_hub(hass, entry)
    async_add_entities(
        [
            AIPrimeRssiSensor(hub),
            AIPrimeFirmwareSensor(hub),
            AIPrimeFanSpeedSensor(hub),
        ]
    )


class _AIPrimeSensorBase(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, hub: AIPrimeHub, suffix: str, name: str) -> None:
        self._hub = hub
        self._attr_unique_id = f"{hub.address}-{suffix}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.address)},
            name=hub.name,
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


class AIPrimeRssiSensor(_AIPrimeSensorBase):
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "rssi", "BLE signal strength")

    @property
    def native_value(self) -> int | None:
        return self._hub.state.rssi

    @property
    def available(self) -> bool:
        # RSSI is meaningful even when our GATT connection is down, as long
        # as advertisements are still being heard.
        return self._hub.state.rssi is not None


class AIPrimeFirmwareSensor(_AIPrimeSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "firmware", "Firmware version")

    @property
    def native_value(self) -> str | None:
        return self._hub.state.firmware


class AIPrimeFanSpeedSensor(_AIPrimeSensorBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "fan_speed", "Fan speed")

    @property
    def native_value(self) -> float | None:
        ch = self._hub.state.channel(CHANNEL_ID_FAN)
        if ch is None:
            return None
        return device_to_percent(ch.value_device)
