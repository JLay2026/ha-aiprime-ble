"""Builders for entity descriptors.

Centralizing entity descriptor construction lets light.py / number.py /
sensor.py stay short and lets us tweak channel labeling in one place once
the channel-name discovery flow lands.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import CHANNEL_DEFAULT_LABELS, CHANNEL_ID_FAN, LED_CHANNEL_IDS


@dataclass(frozen=True)
class ChannelEntityDescriptor:
    """Static info needed to create a per-channel HA entity."""

    channel_id: int
    label: str
    unique_id_suffix: str   # appended to the entry's MAC for the unique_id
    is_fan: bool


def build_led_channel_descriptors() -> list[ChannelEntityDescriptor]:
    """Return one descriptor per LED channel (excludes the fan)."""
    return [
        ChannelEntityDescriptor(
            channel_id=cid,
            label=CHANNEL_DEFAULT_LABELS.get(cid, f"Channel 0x{cid:02X}"),
            unique_id_suffix=f"channel_{cid:02x}",
            is_fan=False,
        )
        for cid in LED_CHANNEL_IDS
    ]


def build_fan_descriptor() -> ChannelEntityDescriptor:
    return ChannelEntityDescriptor(
        channel_id=CHANNEL_ID_FAN,
        label=CHANNEL_DEFAULT_LABELS[CHANNEL_ID_FAN],
        unique_id_suffix="fan",
        is_fan=True,
    )
