"""Diagnostic sensors — RSSI, firmware, fan, and standard 0x180A metadata.

The 0x180A-derived sensors (manufacturer, build version, hardware/software
revision, DI-side serial) are populated by
`protocol.device_info.read_device_info()` once the BLE connection is up.
"""

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
from .schedule_deploy import async_read_active_profile


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = get_hub(hass, entry)
    entry_data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            AIPrimeRssiSensor(hub),
            AIPrimeFirmwareSensor(hub),
            AIPrimeFanSpeedSensor(hub),
            # 0x180A Device Information sensors — populated post-connect.
            AIPrimeManufacturerSensor(hub),
            AIPrimeModelNumberSensor(hub),
            AIPrimeSerialNumberSensor(hub),
            AIPrimeHardwareRevisionSensor(hub),
            AIPrimeSoftwareRevisionSensor(hub),
            # PR-6: which .aip schedule the device is currently running.
            AIPrimeActiveProfileSensor(hub, entry_data),
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
    """FSCI ATTR_FIRMWARE_VERSION (11). Populated by PR-3b's _read_fsci_firmware.

    Distinct from `AIPrimeSoftwareRevisionSensor` (0x180A 2A28) and from the
    `Build version` sensor (0x180A 2A24, which AI populates with version-like
    strings such as "4.2.1.1").
    """

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


# ---------------------------------------------------------------------------
# Standard 0x180A Device Information sensors
# ---------------------------------------------------------------------------

class _AIPrimeDeviceInfoSensor(_AIPrimeSensorBase):
    """Base for sensors backed by a single 0x180A characteristic."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    _attribute_name: str = ""  # subclasses override

    @property
    def native_value(self) -> str | None:
        return getattr(self._hub.state, self._attribute_name, None)


class AIPrimeManufacturerSensor(_AIPrimeDeviceInfoSensor):
    _attribute_name = "manufacturer"

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "manufacturer", "Manufacturer")


class AIPrimeModelNumberSensor(_AIPrimeDeviceInfoSensor):
    """0x180A 2A24 Model Number.

    PR-3b polish (2026-06-02): AI populates this characteristic with what
    looks like a version string (e.g. "4.2.1.1") rather than a model name —
    so the friendly label is "Build version" to reflect the actual content.
    The HA entity_id is preserved (`sensor.<device>_model_number`) so any
    dashboards or automations keyed on the entity_id keep working. The
    unique_id suffix `model_number` is also unchanged.
    """

    _attribute_name = "model_number"

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "model_number", "Build version")


class AIPrimeSerialNumberSensor(_AIPrimeDeviceInfoSensor):
    """Serial number from 0x180A 2A25. May differ from FSCI ATTR_SERIAL (3)."""

    _attribute_name = "serial_number"

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "di_serial_number", "Serial number")


class AIPrimeHardwareRevisionSensor(_AIPrimeDeviceInfoSensor):
    _attribute_name = "hardware_revision"

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "hardware_revision", "Hardware revision")


class AIPrimeSoftwareRevisionSensor(_AIPrimeDeviceInfoSensor):
    _attribute_name = "software_revision"

    def __init__(self, hub: AIPrimeHub) -> None:
        super().__init__(hub, "software_revision", "Software revision")


class AIPrimeActiveProfileSensor(_AIPrimeSensorBase):
    """The .aip schedule profile currently loaded on the device (PR-6).

    Read back from attribute 500 and matched to a known profile in
    <config>/aiprime/profiles/. Shows the profile name, "Unknown" when a
    schedule is loaded that matches no known file, or None when no schedule
    is present. The value is refreshed on add and after each deploy; it lives
    in the per-entry data dict so the deploy path and this sensor share it.
    """

    _attr_icon = "mdi:playlist-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: AIPrimeHub, entry_data) -> None:
        super().__init__(hub, "active_profile", "Active schedule profile")
        self._entry_data = entry_data

    @property
    def native_value(self) -> str | None:
        return self._entry_data.get("active_profile")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # One-shot read so the sensor populates shortly after startup without
        # adding a recurring BLE poll. Deploys also refresh it.
        self.hass.async_create_task(
            async_read_active_profile(self.hass, self._entry_data)
        )
