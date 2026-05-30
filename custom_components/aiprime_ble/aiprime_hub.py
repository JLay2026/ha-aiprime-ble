"""Central hub: owns the BLE connection, holds device state, routes writes.

This is a stub. Day 3-4 of the project plan fills in the actual FSCI write/read
logic. For now the hub:
  - constructs cleanly
  - holds a DeviceState dataclass other modules can read
  - exposes async_setup() / async_unload() so HA can lifecycle it
  - exposes no-op control methods (async_set_channel, async_set_power) so
    the platform files import cleanly and the integration loads
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    ALL_CHANNEL_IDS,
    CHANNEL_DEFAULT_LABELS,
    CHANNEL_ID_FAN,
    CONF_ADDRESS,
    CONF_NAME,
    DOMAIN,
    SIGNAL_AVAILABILITY,
    SIGNAL_STATE_UPDATED,
)
from .types import ChannelState, DeviceState

_LOGGER = logging.getLogger(__name__)


class AIPrimeHub:
    """Owns the BLE session and the in-memory device state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data.get(CONF_ADDRESS, "")
        self.name: str = entry.data.get(CONF_NAME, "AI Prime")
        self.state = DeviceState(address=self.address, name=self.name)
        self._initialize_channels()

    # --- HA lifecycle -----------------------------------------------------

    async def async_setup(self) -> None:
        """Set up the hub. Day 4 will start the BLE connection here."""
        _LOGGER.debug(
            "AIPrimeHub setup for address=%s name=%s (no-op stub)",
            self.address,
            self.name,
        )
        # TODO Day 4: discover BLEDevice via HA bluetooth API and open the
        # GATT connection. For now just mark unavailable.
        self.state.ble_connected = False

    async def async_unload(self) -> None:
        """Tear down BLE connection on entry unload."""
        _LOGGER.debug("AIPrimeHub unload for %s (no-op stub)", self.address)
        self.state.ble_connected = False

    # --- Public control surface ------------------------------------------
    # These are called by light.py and number.py. They're stubs until Day 4.

    async def async_set_channel(self, channel_id: int, value_device: int) -> None:
        """Set a single channel to a device-scale (0-1000) value."""
        if channel_id not in self.state.channels:
            _LOGGER.warning(
                "Refusing to set unknown channel 0x%02X", channel_id
            )
            return

        clamped = max(0, min(1000, int(value_device)))
        self.state.channels[channel_id].value_device = clamped
        _LOGGER.debug(
            "STUB set channel 0x%02X -> %d (would FSCI-write here)",
            channel_id,
            clamped,
        )
        self._notify_state_changed()

    async def async_set_power(self, *, on: bool) -> None:
        """Aggregate power. Off = all LED channels to 0 (fan unchanged)."""
        if on:
            # No "remembered last state" yet; bring up to 50% as a placeholder.
            placeholder = 500
            for cid in self.state.channels:
                if cid != CHANNEL_ID_FAN:
                    self.state.channels[cid].value_device = placeholder
        else:
            for cid in self.state.channels:
                if cid != CHANNEL_ID_FAN:
                    self.state.channels[cid].value_device = 0

        _LOGGER.debug(
            "STUB set power=%s (would FSCI-write per channel here)", on
        )
        self._notify_state_changed()

    # --- Read helpers used by entities -----------------------------------

    def is_on(self) -> bool:
        """Aggregate on/off: any LED channel above zero."""
        return any(
            cs.value_device > 0
            for cid, cs in self.state.channels.items()
            if cid != CHANNEL_ID_FAN
        )

    def aggregate_brightness_device(self) -> int:
        """Aggregate brightness as the max of all LED channels (0-1000)."""
        led_values = [
            cs.value_device
            for cid, cs in self.state.channels.items()
            if cid != CHANNEL_ID_FAN
        ]
        return max(led_values) if led_values else 0

    # --- Internal helpers ------------------------------------------------

    def _initialize_channels(self) -> None:
        for cid in ALL_CHANNEL_IDS:
            self.state.channels[cid] = ChannelState(
                channel_id=cid,
                label=CHANNEL_DEFAULT_LABELS.get(cid, f"Channel 0x{cid:02X}"),
                value_device=0,
                is_fan=(cid == CHANNEL_ID_FAN),
            )

    def _notify_state_changed(self) -> None:
        async_dispatcher_send(
            self.hass,
            SIGNAL_STATE_UPDATED.format(entry=self.entry.entry_id),
        )

    def _notify_availability_changed(self) -> None:
        async_dispatcher_send(
            self.hass,
            SIGNAL_AVAILABILITY.format(entry=self.entry.entry_id),
            self.state.ble_connected,
        )

    @property
    def signal_state_updated(self) -> str:
        return SIGNAL_STATE_UPDATED.format(entry=self.entry.entry_id)

    @property
    def signal_availability(self) -> str:
        return SIGNAL_AVAILABILITY.format(entry=self.entry.entry_id)


def get_hub(hass: HomeAssistant, entry: ConfigEntry) -> AIPrimeHub:
    """Convenience accessor used by platform setup functions."""
    domain_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    return domain_data["hub"]
