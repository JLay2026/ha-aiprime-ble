"""Standard BLE Device Information (0x180A) reader.

The AI Prime exposes the entire standard 0x180A Device Information Service
(discovered during Day 3 validation), which gives us manufacturer, model,
serial, hardware/firmware/software revisions, system ID, regulatory cert,
and PnP ID without going through the proprietary FSCI codec.

This module is intentionally a thin wrapper around bleak — pure async
functions, no Home Assistant imports. The hub calls it once after BLE
connect; the sensor platform reads the resulting fields off DeviceState.

Char UUIDs are the 16-bit standard ones expanded into the 128-bit base
UUID format (`0000XXXX-0000-1000-8000-00805f9b34fb`) so they compare cleanly
against bleak's discovery output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..const import (
    CHAR_DI_FIRMWARE_REV,
    CHAR_DI_HARDWARE_REV,
    CHAR_DI_MANUFACTURER,
    CHAR_DI_MODEL_NUMBER,
    CHAR_DI_SERIAL_NUMBER,
    CHAR_DI_SOFTWARE_REV,
)

if TYPE_CHECKING:
    from bleak import BleakClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Standard 0x180A characteristics, all optional (some chars may be missing)."""

    manufacturer: str | None = None
    model_number: str | None = None
    serial_number: str | None = None
    hardware_revision: str | None = None
    firmware_revision: str | None = None
    software_revision: str | None = None


async def read_device_info(client: "BleakClient") -> DeviceInfo:
    """Best-effort read of every standard 0x180A char we care about.

    Each char is read independently; a missing/unreadable char yields None
    in the corresponding field rather than aborting the whole read. This
    matches the BLE spec where every 0x180A characteristic is optional.
    """
    info = DeviceInfo()
    chars: list[tuple[str, str]] = [
        ("manufacturer", CHAR_DI_MANUFACTURER),
        ("model_number", CHAR_DI_MODEL_NUMBER),
        ("serial_number", CHAR_DI_SERIAL_NUMBER),
        ("hardware_revision", CHAR_DI_HARDWARE_REV),
        ("firmware_revision", CHAR_DI_FIRMWARE_REV),
        ("software_revision", CHAR_DI_SOFTWARE_REV),
    ]
    for field, uuid in chars:
        try:
            raw = await client.read_gatt_char(uuid)
        except Exception as err:  # noqa: BLE001 — best-effort per-char
            _LOGGER.debug("0x180A %s read failed: %s", field, err)
            continue
        value = _decode_utf8(raw)
        if value is not None:
            setattr(info, field, value)
    return info


def _decode_utf8(raw: bytes) -> str | None:
    """Decode bytes to a stripped UTF-8 string, returning None on failure."""
    if not raw:
        return None
    try:
        text = raw.decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001 — extra paranoid
        return None
    return text or None
