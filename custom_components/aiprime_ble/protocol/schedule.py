"""FSCI schedule-deploy codec for the AI Prime HD lighting product.

Pure, offline (no BLE I/O). Turns a parsed ``.aip`` :class:`~.aip.Profile`
into the exact 3-frame FSCI sequence the myAI app sends to deploy a schedule,
verified byte-for-byte (CRC included) against a real PacketLogger capture
(``deploy_gregory_frames.txt`` fixture, msgid 8/9/10).

Decoded 2026-06-11 from the capture. The deploy is three FSCI SET frames on
CHAR_TX_DATA, AFTER the connect-time priming GET reads:

  1. SET attr 500 (schedule), reserved = 0x0000  -- a MULTI-attribute frame:
       attr 500: inst 0, count = N_active + 1, itemLen = 24, then the points;
       attr 511: inst 0, count 1, itemLen 2, value = 1000 (0x03E8).
  2. SET attr 510, reserved = 0x0004, value = 00 00 01 01   (commit step 1)
  3. SET attr 510, reserved = 0x0004, value = 00 00 01 03   (commit step 2)

Point record (24 bytes), little-endian:
    [time:2 (minute-of-day)][flag:1][7 x (channel_id:1, intensity:2)]
channels in CHANNEL_WRITE_ORDER = [blue, green, deep_red, moonlight,
warm_white, cool_white, MASTER].

Rules proven against the capture:
  * time = 0 is implicit and dropped; only points with minute > 0 are emitted,
    followed by ONE all-zero (24 x 0x00) null terminator. count = N_active + 1.
  * MASTER (0x01) = 1000 in every active point (even all-off points), 0 in the
    terminator.
  * flag is POSITIONAL over the active points: first = 0x07, last = 0x03,
    second-to-last = 0x0b, all other middles = 0x01, terminator = 0x00.
  * intensity = the native .aip value (0..2000) CLAMPED to a per-channel max.
    Verified maxes: green (0x13) = 1000, cool_white (0x10) = 1000,
    deep_red (0x19) >= 2000 (HD channel, passes 2000 through).
    UNVERIFIED (assumed pass-through / 2000) until a capture exercises them
    above 1000: blue (0x11), warm_white (0x16), moonlight (0x1E).

See memory [[aiprime-aip-schedule-format]] and the project HANDOFF.
"""
from __future__ import annotations

import struct
from typing import Iterable

from .aip import AIP_COLORS, INTENSITY_MAX_AIP, MINUTES_PER_DAY, Profile
from .fsci import crc16
from ..const import ATTR_SCHEDULE, CHANNEL_WRITE_ORDER

__all__ = [
    "ATTR_SCHEDULE_AUX",
    "ATTR_SCHEDULE_COMMIT",
    "CHANNEL_MAX_INTENSITY",
    "COLOR_TO_CHANNEL",
    "MASTER_CHANNEL_ID",
    "build_deploy_sequence",
    "build_commit_frame",
    "build_schedule_set_frame",
    "profile_to_points",
]

# --- FSCI frame constants (mirror of fsci.py; schedule uses distinct reserved)
_STX = 0x02
_OP_GROUP_REQUEST = 0xDE
_OP_CODE_SET = 0x18
_RESERVED_SCHEDULE = b"\x00\x00"   # attr-500 multi-attr SET uses 0x0000
_RESERVED_COMMIT = b"\x04\x00"     # attr-510 commit SETs use 0x0004

# --- Schedule attribute IDs ------------------------------------------------
ATTR_SCHEDULE_AUX = 511     # written alongside attr 500; value = 1000
ATTR_SCHEDULE_COMMIT = 510  # commit/activate; values below replayed verbatim

_AUX_511_VALUE = 1000
_COMMIT_VALUES = (b"\x00\x00\x01\x01", b"\x00\x00\x01\x03")

# --- Point / channel layout ------------------------------------------------
SCHEDULE_ITEM_LEN = 24
MASTER_CHANNEL_ID = 0x01
MASTER_VALUE = 1000

# Color name -> device channel ID (slider tests + deploy capture, 2026-06-11).
COLOR_TO_CHANNEL: dict[str, int] = {
    "blue": 0x11,
    "green": 0x13,
    "deep_red": 0x19,
    "moonlight": 0x1E,
    "warm_white": 0x16,
    "cool_white": 0x10,
}
_CHANNEL_TO_COLOR = {cid: color for color, cid in COLOR_TO_CHANNEL.items()}

# Per-channel intensity ceiling applied to the native .aip value before it
# goes on the wire. Default = INTENSITY_MAX_AIP (2000) i.e. no extra clamp.
# Only entries that DIFFER from the default are listed; values proven from the
# gregory capture. See module docstring for the (un)verified status of each.
CHANNEL_MAX_INTENSITY: dict[int, int] = {
    0x13: 1000,  # green       -- proven clamp
    0x10: 1000,  # cool_white  -- proven clamp
    # 0x19 deep_red: 2000 (HD, passes through -> default)
    # 0x11 blue / 0x16 warm_white / 0x1E moonlight: UNVERIFIED, default 2000
}


def _clamp(channel_id: int, intensity: int) -> int:
    ceiling = CHANNEL_MAX_INTENSITY.get(channel_id, INTENSITY_MAX_AIP)
    if intensity < 0:
        return 0
    return intensity if intensity <= ceiling else ceiling


def _active_times(profile: Profile) -> list[int]:
    """Sorted union of all colors' point minutes, excluding the implicit 0."""
    times: set[int] = set()
    for color in AIP_COLORS:
        for p in profile.points(color):
            times.add(p.minute % MINUTES_PER_DAY)
    times.discard(0)
    return sorted(times)


def _flag_for(index: int, n: int) -> int:
    """Positional flag byte for active point `index` of `n` total."""
    if index == 0:
        return 0x07
    if index == n - 1:
        return 0x03
    if index == n - 2:
        return 0x0B
    return 0x01


def profile_to_points(profile: Profile) -> list[tuple[int, int, dict[int, int]]]:
    """Build the active schedule points from a parsed profile.

    Returns a list of ``(minute, flag, {channel_id: intensity})`` for each
    active time (minute > 0), in ascending order. MASTER is included at 1000.
    The null terminator is NOT included here (the frame builder appends it).
    """
    times = _active_times(profile)
    n = len(times)
    points: list[tuple[int, int, dict[int, int]]] = []
    for i, t in enumerate(times):
        chans: dict[int, int] = {}
        for cid in CHANNEL_WRITE_ORDER:
            if cid == MASTER_CHANNEL_ID:
                chans[cid] = MASTER_VALUE
                continue
            color = _CHANNEL_TO_COLOR[cid]
            raw = round(profile.value_at(color, t))
            chans[cid] = _clamp(cid, raw)
        points.append((t, _flag_for(i, n), chans))
    return points


def _encode_point(minute: int, flag: int, chans: dict[int, int]) -> bytes:
    out = bytearray(struct.pack("<HB", minute & 0xFFFF, flag & 0xFF))
    for cid in CHANNEL_WRITE_ORDER:
        out += bytes([cid]) + struct.pack("<H", chans.get(cid, 0) & 0xFFFF)
    return bytes(out)


def _build_set_frame(msg_id: int, reserved: bytes, payload: bytes) -> bytes:
    inner = bytearray()
    inner += bytes([_OP_GROUP_REQUEST, _OP_CODE_SET])
    inner += struct.pack("<H", msg_id & 0xFFFF)
    inner += reserved
    inner += struct.pack("<H", len(payload))
    inner += payload
    crc = crc16(bytes(inner))
    return bytes([_STX]) + bytes(inner) + struct.pack("<H", crc)


def build_schedule_set_frame(
    points: list[tuple[int, int, dict[int, int]]],
    msg_id: int,
) -> bytes:
    """Frame 1: multi-attr SET (attr 500 schedule + attr 511), reserved 0x0000."""
    items = b"".join(_encode_point(t, flag, ch) for t, flag, ch in points)
    items += bytes(SCHEDULE_ITEM_LEN)  # null terminator (24 x 0x00)
    count = len(points) + 1
    group500 = (
        struct.pack("<HBBB", ATTR_SCHEDULE, 0, count, SCHEDULE_ITEM_LEN) + items
    )
    group511 = struct.pack("<HBBB", ATTR_SCHEDULE_AUX, 0, 1, 2) + struct.pack(
        "<H", _AUX_511_VALUE
    )
    return _build_set_frame(msg_id, _RESERVED_SCHEDULE, group500 + group511)


def build_commit_frame(value: bytes, msg_id: int) -> bytes:
    """Frame 2/3: SET attr 510 commit, reserved 0x0004."""
    payload = struct.pack("<HBBB", ATTR_SCHEDULE_COMMIT, 0, 1, len(value)) + value
    return _build_set_frame(msg_id, _RESERVED_COMMIT, payload)


def build_deploy_sequence(
    profile: Profile, start_msg_id: int
) -> list[tuple[int, bytes]]:
    """Full deploy: returns [(msg_id, frame)] for the 3 frames.

    `start_msg_id` is the msg_id of the schedule SET; the two commits use
    `start_msg_id + 1` and `start_msg_id + 2`.
    """
    points = profile_to_points(profile)
    frames = [(start_msg_id, build_schedule_set_frame(points, start_msg_id))]
    for offset, value in enumerate(_COMMIT_VALUES, start=1):
        mid = start_msg_id + offset
        frames.append((mid, build_commit_frame(value, mid)))
    return frames
