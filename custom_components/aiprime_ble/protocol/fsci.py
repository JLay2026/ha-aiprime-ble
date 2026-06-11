"""FSCI codec for the AI Prime HD lighting product.

Lifted from `mpshevlotsky/ai-pump-feed-esp32/core/protocol/fsci.py` after Day 3
validation (2026-06-02) confirmed that the pump's wire format, CRC, message ID
counter, and GATT UUIDs port bit-identically to the lighting product's
Qualcomm QCA4020 chip. MicroPython try/except shims are removed; pump-specific
attribute constants and prebuilt payloads are dropped in favor of
lighting-specific frame builders that consume the attribute IDs in
`..const`.

Day 3 validation evidence:
    TX (15B): 02 DE 17 01 00 00 00 04 00 03 00 00 01 47 4A
    RX (31B): 02 DF 17 01 00 00 00 14 00 00 03 00 00 01 0E
              41 30 39 46 30 41 45 32 44 30 52 31 43 46 DE 08
    → status=0x00, payload ASCII "A09F0AE2D0R1CF" (serial)

Frame layout, little-endian throughout:
    [STX=0x02]
    [opGroup][opCode][msgId:2][reserved:2][payloadLen:2]
    [payload...]
    [crc16:2]
opGroup: 0xDE REQUEST / 0xDF CONFIRM.
opCode:  0x17 GET / 0x18 SET.
reserved: 00 00 for GET, 04 00 for SET (pump-project convention; held).
CRC16-CCITT (poly 0x1021, init 0xFFFF) covers bytes AFTER STX through end of
payload — excludes STX and the CRC bytes themselves.

GET payload: [attrId:2][instance:1][count:1]
    count=0xFF means "all instances".
SET payload: [attrId:2][instance:1][count:1][itemLen:1][value...]

This module is pure: no BLE I/O. The hub owns the BleakClient and pumps frames
through TX_DATA → reads CONFIRMs from RX_DATA + RX_FINAL.
"""

from __future__ import annotations

import struct
from typing import Optional

from ..const import (
    ATTR_LIVE_CHANNEL_CONTROL,
    ATTR_LIVE_CHANNEL_STATE,
    ATTR_LIVE_CHANNEL_TARGET,
    ATTR_MESH_LOCAL_ADDRESSES,
    CHANNEL_STATE_ITEM_LEN,
    CHANNEL_WRITE_ORDER,
    DEVICE_VALUE_MAX,
    DEVICE_WRITE_VALUE_MAX,
    RAMP_SLIDER,
)

__all__ = [
    "FsciCodec",
    "STATUS_SUCCESS",
    "crc16",
    "parse_get_attribute_payload",
    "parse_response_status",
    "status_name",
    "to_hex",
]


# ---------------------------------------------------------------------------
# Frame constants
# ---------------------------------------------------------------------------

_STX = 0x02
_OP_GROUP_REQUEST = 0xDE
_OP_GROUP_CONFIRM = 0xDF
_OP_CODE_GET = 0x17
_OP_CODE_SET = 0x18

_RESERVED_GET = b"\x00\x00"
_RESERVED_SET = b"\x04\x00"

_MSG_ID_WRAP = 20000


# ---------------------------------------------------------------------------
# Status codes
# ---------------------------------------------------------------------------

STATUS_SUCCESS = 0x00

_STATUS_NAMES = {
    0x00: "Success",
    0x01: "Failed",
    0x02: "InvalidInstance",
    0x03: "InvalidElement",
    0x04: "NotPermitted",
    0x05: "InvalidMode",
    0x06: "NoMem",
    0x07: "UnsupportedAttribute",
    0x08: "EmptyEntry",
    0x09: "InvalidValue",
    0x0A: "AlreadyConnected",
    0x0B: "AlreadyCreated",
    0x0C: "NoTimers",
    0x0D: "InvalidRequest",
    0x0E: "InvalidDeviceType",
    0x0F: "InvalidPrimitiveType",
    0x10: "Timeout",
    0x11: "Busy",
    0x14: "InvalidRange",
    0x15: "InvalidSize",
    0xFF: "EntryNotFound",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def crc16(data: bytes) -> int:
    """CRC16-CCITT: polynomial 0x1021, initial value 0xFFFF."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


def status_name(code: int) -> str:
    """Human-readable name for a FSCI status byte."""
    return _STATUS_NAMES.get(code, f"Unknown(0x{code:02X})")


def to_hex(data: Optional[bytes]) -> str:
    """Space-separated hex string for debug logging. Returns 'null' on None."""
    if data is None:
        return "null"
    return " ".join(f"{b:02X}" for b in data)


def parse_response_status(frame: Optional[bytes]) -> int:
    """Extract the FSCI status byte from any CONFIRM frame.

    Returns the status byte (0x00 = success) on success, -1 if the frame is
    too short, malformed, or not a CONFIRM.
    """
    if frame is None or len(frame) < 12:
        return -1
    if frame[1] != _OP_GROUP_CONFIRM:
        return -1
    payload_len = frame[7] | (frame[8] << 8)
    if payload_len < 1:
        return -1
    return frame[9]


def parse_get_attribute_payload(
    frame: Optional[bytes],
    expected_attr_id: int,
) -> list[bytes]:
    """Extract attribute values from a GET CONFIRM frame.

    Returns a list of per-instance byte payloads (one entry per `count` in the
    request), or an empty list if the frame is malformed, status != SUCCESS,
    or the attribute ID doesn't match.

    GET CONFIRM payload layout (after [status:1]):
        [attrId:2][instance:1][count:1][itemLen:1][values...]

    Each value is `itemLen` bytes long; this returns them as raw bytes so
    callers can decode (ASCII serial, uint16 channel value, IPv6 address, …).
    """
    if frame is None or len(frame) < 15:
        return []
    if frame[1] != _OP_GROUP_CONFIRM:
        return []
    payload_len = frame[7] | (frame[8] << 8)
    if payload_len < 6:
        return []
    if frame[9] != STATUS_SUCCESS:
        return []

    pos = 10
    payload_end = min(9 + payload_len, len(frame) - 2)  # exclude CRC
    results: list[bytes] = []
    while pos + 5 <= payload_end:
        attr_id = frame[pos] | (frame[pos + 1] << 8)
        # frame[pos + 2] is instance — ignored here, caller knows context
        count = frame[pos + 3]
        item_len = frame[pos + 4]
        pos += 5
        if attr_id != expected_attr_id:
            # Skip past this attribute group and continue scanning
            pos += count * item_len
            continue
        for _ in range(count):
            if pos + item_len > payload_end:
                pos += item_len
                continue
            results.append(bytes(frame[pos : pos + item_len]))
            pos += item_len
    return results


# ---------------------------------------------------------------------------
# FsciCodec — owns the message ID counter, builds frames
# ---------------------------------------------------------------------------

class FsciCodec:
    """Builds FSCI request frames. One instance per device hub.

    The only mutable state is the message ID counter, which rotates 1..19999
    so we can match CONFIRMs to outstanding requests.
    """

    def __init__(self) -> None:
        self._msg_id: int = 0

    # --- Generic frame builders ----------------------------------------

    def build_get_attribute(
        self,
        attr_id: int,
        instance: int = 0,
        count: int = 1,
    ) -> tuple[int, bytes]:
        """Build a GET frame for an arbitrary attribute.

        Returns `(msg_id, frame_bytes)` so the caller can register the
        in-flight request before writing the frame to TX_DATA.
        """
        payload = struct.pack("<HBB", attr_id, instance, count)
        return self._build_frame(_OP_CODE_GET, payload)

    def build_set_attribute(
        self,
        attr_id: int,
        value: bytes,
        instance: int = 0,
    ) -> tuple[int, bytes]:
        """Build a SET frame for a single attribute with an arbitrary value.

        Payload layout: [attrId:2][instance:1][count=1][itemLen:1][value...]
        """
        payload = struct.pack("<HBBB", attr_id, instance, 1, len(value)) + value
        return self._build_frame(_OP_CODE_SET, payload)

    # --- Lighting-specific convenience builders ------------------------

    def build_handshake(self) -> tuple[int, bytes]:
        """Connection-init handshake: GET MeshLocalAddresses (attr 1005).

        Mirrors the pump project's `build_handshake_packet`. The lighting
        product accepts this too (validated via attribute 3 round-trip);
        whether it returns useful mesh data is verified in PR-2.
        """
        payload = struct.pack(
            "<HBB", ATTR_MESH_LOCAL_ADDRESSES, 0, 0xFF
        )
        return self._build_frame(_OP_CODE_GET, payload)

    def build_get_channel_state(self) -> tuple[int, bytes]:
        """GET all live channel state values (attr 1500), all instances."""
        payload = struct.pack(
            "<HBB", ATTR_LIVE_CHANNEL_STATE, 0, 0xFF
        )
        return self._build_frame(_OP_CODE_GET, payload)

    def build_get_channel_targets(self) -> tuple[int, bytes]:
        """GET all channel target values (attr 1504), all instances.

        NOTE: as of the 2026-06-02 hot-fix, ATTR_LIVE_CHANNEL_TARGET is an
        alias for ATTR_LIVE_CHANNEL_STATE (both = 1504). This builder is
        kept for backward-compat callers; new code should use
        build_get_channel_state.
        """
        payload = struct.pack(
            "<HBB", ATTR_LIVE_CHANNEL_TARGET, 0, 0xFF
        )
        return self._build_frame(_OP_CODE_GET, payload)

    def build_set_channel(
        self, channel_id: int, value_device: int
    ) -> tuple[int, bytes]:
        """SET a single channel to a raw device value (0..DEVICE_VALUE_MAX).

        PR-3c (2026-06-06): fixed two bugs vs the v0.0.1 stub:
          - value is now uint32 LE (4 bytes), matching the read-side
            CHANNEL_STATE_ITEM_LEN. Was uint16 LE (2 bytes), inherited
            from the pump project's per-mille scale.
          - clamp ceiling is now DEVICE_VALUE_MAX (20000), matching the
            hot-fixed scale. Was 1000, which would have silently capped
            every write at ~5%.

        DEPRECATED by PR-4 (2026-06-10): the device does NOT apply writes to
        attribute 1504/1513 — those are read-only live-state views. The real
        control path is build_set_all_channels (attribute 407). This builder
        is retained only for reference / potential probing; the hub no longer
        calls it.

        Uses `instance = channel_id` (the FSCI convention for "which
        sub-thing of this attribute").
        """
        clamped = max(0, min(DEVICE_VALUE_MAX, int(value_device)))
        value_bytes = struct.pack("<I", clamped)
        payload = struct.pack(
            "<HBBB",
            ATTR_LIVE_CHANNEL_TARGET,
            channel_id,                       # instance
            1,                                # count
            CHANNEL_STATE_ITEM_LEN,           # itemLen = 4
        ) + value_bytes
        return self._build_frame(_OP_CODE_SET, payload)


    def build_set_all_channels(
        self,
        values: dict[int, int],
        ramp: int = RAMP_SLIDER,
    ) -> tuple[int, bytes]:
        """SET all channels at once via attribute 407 — the myAI control path.

        DECODED 2026-06-10 from an iOS HCI (PacketLogger) capture of the myAI
        app. The device's live-control write target is attribute 407 (0x0197),
        written as a single bulk frame carrying ALL channels — NOT the
        per-channel writes to 1504/1513 attempted in PR-3c/3d (those are
        read-only live-state views that silently ACK-discard writes). See
        memory note [[aiprime-write-protocol-decoded]].

        `values` maps channel_id -> 0..DEVICE_WRITE_VALUE_MAX (per-mille,
        0..1000). Missing channels default to 0. `ramp` is the fade byte
        (RAMP_SLIDER 0x0a for slider-style, RAMP_POWER 0x3c for on/off).

        45-byte value layout (verified byte-identical to myAI, CRC included):
            [0]      0x01            const
            [1:4]    00 00 00        const
            [4]      0x01            const
            [5]      0x00            const
            [6]      ramp            fade byte
            [7:24]   17x 0x00        zero pad
            [24:45]  7x [id:1][value:uint16 LE]  in CHANNEL_WRITE_ORDER
        """
        header = bytes([0x01, 0x00, 0x00, 0x00, 0x01, 0x00, ramp & 0xFF]) + bytes(17)
        chan_bytes = bytearray()
        for cid in CHANNEL_WRITE_ORDER:
            v = max(0, min(DEVICE_WRITE_VALUE_MAX, int(values.get(cid, 0))))
            chan_bytes += bytes([cid]) + struct.pack("<H", v)
        value = header + bytes(chan_bytes)
        payload = struct.pack(
            "<HBBB", ATTR_LIVE_CHANNEL_CONTROL, 0, 1, len(value)
        ) + value
        return self._build_frame(_OP_CODE_SET, payload)

    # --- Internals -----------------------------------------------------

    def _next_msg_id(self) -> int:
        self._msg_id = (self._msg_id % _MSG_ID_WRAP) + 1
        return self._msg_id

    def _build_frame(self, op_code: int, payload: bytes) -> tuple[int, bytes]:
        """Assemble a complete frame and return (msg_id, frame_bytes)."""
        msg_id = self._next_msg_id()
        reserved = _RESERVED_SET if op_code == _OP_CODE_SET else _RESERVED_GET

        inner = bytearray(8 + len(payload))
        inner[0] = _OP_GROUP_REQUEST
        inner[1] = op_code
        struct.pack_into("<H", inner, 2, msg_id)
        inner[4] = reserved[0]
        inner[5] = reserved[1]
        struct.pack_into("<H", inner, 6, len(payload))
        inner[8:] = payload

        crc = crc16(bytes(inner))

        frame = bytearray(1 + len(inner) + 2)
        frame[0] = _STX
        frame[1 : 1 + len(inner)] = inner
        struct.pack_into("<H", frame, 1 + len(inner), crc)
        return msg_id, bytes(frame)
