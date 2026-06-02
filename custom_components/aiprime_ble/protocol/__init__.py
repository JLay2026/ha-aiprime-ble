"""FSCI protocol layer for the AI Prime HD lighting product.

Day 3 validation (2026-06-02) confirmed the pump-project framing applies
bit-identically. Public surface:

- `FsciCodec` — frame builder with rotating msg_id; one per hub.
- `parse_response_status` — generic CONFIRM status extractor.
- `parse_get_attribute_payload` — extract per-instance values from a GET CONFIRM.
- `crc16`, `status_name`, `to_hex`, `STATUS_SUCCESS` — utility helpers.
- `device_info` submodule — standard 0x180A Device Information Service reader.
"""

from __future__ import annotations

from .device_info import DeviceInfo, read_device_info
from .fsci import (
    STATUS_SUCCESS,
    FsciCodec,
    crc16,
    parse_get_attribute_payload,
    parse_response_status,
    status_name,
    to_hex,
)

__all__ = [
    "DeviceInfo",
    "FsciCodec",
    "STATUS_SUCCESS",
    "crc16",
    "parse_get_attribute_payload",
    "parse_response_status",
    "read_device_info",
    "status_name",
    "to_hex",
]
