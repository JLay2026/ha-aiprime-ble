from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChannelState:
    """Live state for one device channel (LED or fan)."""

    channel_id: int                 # e.g. 0x10
    label: str                      # user-visible name ("Cool White") or default
    value_device: int = 0           # 0-1000 per-mille
    is_fan: bool = False


@dataclass
class DeviceState:
    """Snapshot of the light's live state, as exposed to HA entities."""

    address: str = ""               # BLE MAC, e.g. "1C:BC:EC:0A:E2:D0"
    name: str = ""                  # MOBIUS or user-set name
    serial: str | None = None
    firmware: str | None = None
    ble_connected: bool = False
    rssi: int | None = None
    channels: dict[int, ChannelState] = field(default_factory=dict)

    def channel(self, channel_id: int) -> ChannelState | None:
        return self.channels.get(channel_id)


@dataclass
class Scene:
    """A scene definition from attribute 400."""

    scene_id: int
    name: str
    duration_seconds: int
    channel_values: dict[int, int]   # channel_id -> 0..1000


@dataclass
class ScheduleEvent:
    """One event from attribute 500."""

    time_minutes_since_midnight: int
    flags: int
    channel_values: dict[int, int]


def coerce_int(value: Any) -> int | None:
    """Coerce a value to int, returning None on failure."""
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
