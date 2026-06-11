from __future__ import annotations

DOMAIN = "aiprime_ble"

# --- Config entry keys -----------------------------------------------------
CONF_ADDRESS = "address"
CONF_NAME = "name"

# --- Timing / connection tuning -------------------------------------------
DEFAULT_RECONNECT_BACKOFF_INITIAL_S = 1.0
DEFAULT_RECONNECT_BACKOFF_CAP_S = 30.0
DEFAULT_STATE_POLL_INTERVAL_S = 30.0   # how often to re-read live channel state
DEFAULT_CONNECT_TIMEOUT_S = 10.0

# --- Platforms ------------------------------------------------------------
PLATFORMS: list[str] = ["light", "number", "sensor"]

# --- Proprietary GATT (FSCI transport, lifted from pump project; validated
# Day 3 against the AI Prime QCA4020 — bit-identical round-trip) -----------
SERVICE_GENERAL = "01ff0100-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
SERVICE_OTAP = "01ff5550-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_RX_DATA = "01ff0101-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_RX_FINAL = "01ff0102-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_TX_DATA = "01ff0103-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
CHAR_TX_FINAL = "01ff0104-ba5e-f4ee-5ca1-eb1e5e4b1ce0"
# 5th characteristic discovered during Day 3; not in pump project's UUID list.
# `[write-without-response, notify]`. Purpose TBD — candidates: bulk-write
# streaming, push notifications, OTA fast path. Held here for future use.
CHAR_AUX = "01ff0105-ba5e-f4ee-5ca1-eb1e5e4b1ce0"

# --- Standard 0x180A Device Information Service ---------------------------
# Read once at connect; populates DeviceState metadata fields. Each char is
# optional per the BLE spec — missing chars yield None, not an error.
SERVICE_DEVICE_INFO = "0000180a-0000-1000-8000-00805f9b34fb"
CHAR_DI_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
CHAR_DI_MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"
CHAR_DI_SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"
CHAR_DI_HARDWARE_REV = "00002a27-0000-1000-8000-00805f9b34fb"
CHAR_DI_FIRMWARE_REV = "00002a26-0000-1000-8000-00805f9b34fb"
CHAR_DI_SOFTWARE_REV = "00002a28-0000-1000-8000-00805f9b34fb"
CHAR_DI_SYSTEM_ID = "00002a23-0000-1000-8000-00805f9b34fb"
CHAR_DI_REGULATORY_CERT = "00002a2a-0000-1000-8000-00805f9b34fb"
CHAR_DI_PNP_ID = "00002a50-0000-1000-8000-00805f9b34fb"

# --- Channel layout (from Mobius app Settings Dump, attribute 901) --------
# 6 LED channels + 1 fan, raw device value range 0-20000 (4-byte uint32 LE).
# See `_async_read_channel_state` in aiprime_hub.py + the hot-fix validation
# notes at the bottom of this file for the byte-encoding rationale.
CHANNEL_ID_FAN = 0x01

LED_CHANNEL_IDS: tuple[int, ...] = (
    0x10,
    0x11,
    0x13,
    0x16,
    0x19,
    0x1E,
)

ALL_CHANNEL_IDS: tuple[int, ...] = (CHANNEL_ID_FAN, *LED_CHANNEL_IDS)

# Channel-name mapping is empirically discovered at first run; placeholder
# labels live here until users (or maintainers) confirm them.
CHANNEL_DEFAULT_LABELS: dict[int, str] = {
    0x01: "Fan",
    0x10: "Channel 0x10",
    0x11: "Channel 0x11",
    0x13: "Channel 0x13",
    0x16: "Channel 0x16",
    0x19: "Channel 0x19",
    0x1E: "Channel 0x1E",
}

# --- Value-scale conversions ----------------------------------------------
# Hot-fix 2026-06-02: scale changed from 0-1000 (per-mille assumption from
# pump project) to 0-20000 after the channel-state probe revealed AI Prime
# returns uint32 LE values reaching ~19920 when channels are near 100%
# (e.g., 0x10 at ~99.6% returned 0x4DD0 = 19920). 20000 = nominal max.
# Calibration may need a small tweak (e.g., 20000 might really be 19999 or
# the device may clip slightly above 20000); revisit if write feedback
# shows asymmetry between set and read values.
DEVICE_VALUE_MAX = 20000  # raw device units — full scale (READ path, attr 1504)
USER_VALUE_MAX = 100      # percent — what HA Number entities expose


def percent_to_device(pct: float) -> int:
    """Convert 0-100 user percent into 0-DEVICE_VALUE_MAX raw device units."""
    if pct <= 0:
        return 0
    if pct >= USER_VALUE_MAX:
        return DEVICE_VALUE_MAX
    return round(pct * DEVICE_VALUE_MAX / USER_VALUE_MAX)


def device_to_percent(value: int) -> float:
    """Convert raw 0-DEVICE_VALUE_MAX device units into 0-100 user percent."""
    if value <= 0:
        return 0.0
    if value >= DEVICE_VALUE_MAX:
        return float(USER_VALUE_MAX)
    return value * USER_VALUE_MAX / DEVICE_VALUE_MAX


# --- Known FSCI attribute IDs ---------------------------------------------
ATTR_SERIAL = 3              # ASCII serial string (validated Day 3: "A09F0AE2D0R1CF")
ATTR_FIRMWARE_VERSION = 11   # populated by PR-3b's _read_fsci_firmware
ATTR_TIMEZONE_POSIX = 205
ATTR_TIMEZONE_NAME = 206
ATTR_MESH_LOCAL_ADDRESSES = 1005
ATTR_BLE_MAC = 1603
ATTR_CHANNEL_LIST = 901

# --- Channel state attributes (hot-fix 2026-06-02 confirmed via probe) ----
# `aiprime_channel_probe.py` queried all three candidates against all 7
# discovered channel IDs while the schedule was driving the LEDs:
#
#   1500 (was ATTR_LIVE_CHANNEL_STATE):
#     - 2-byte payload, always returns 0x0000 for every channel.
#     - Appears to be a STATUS / FLAG word, not the live brightness.
#     - Renamed to ATTR_CHANNEL_STATUS_WORD; not currently used by the poll.
#
#   1504 (was ATTR_LIVE_CHANNEL_TARGET, NOW the polled attribute):
#     - 4-byte payload, uint32 LE in 0..~20000.
#     - Returned non-zero values for 0x10/0x11/0x13/0x16/0x19 matching
#       what the schedule was driving (~19920 = 99.6%, etc.).
#     - Returned InvalidElement (status 0x03) for 0x01 (fan) and 0x1E.
#     - PR-3c/3d (2026-06-07): WRITES to 1504 AND 1513 return status=SUCCESS
#       but the device never physically applies them. These are READ-ONLY
#       live-state views. The real control-write path is attribute 407
#       (ATTR_LIVE_CHANNEL_CONTROL below), decoded from myAI 2026-06-10.
#
#   1513 (ATTR_LIVE_CHANNEL_LAST_SET):
#     - 4-byte payload, mirror of 1504 on read; also ACK-discards writes.
#
ATTR_CHANNEL_STATUS_WORD = 1500   # 2-byte status flag; always 0 in current observations
ATTR_LIVE_CHANNEL_STATE = 1504    # 4-byte uint32 LE; READ target for live state
ATTR_LIVE_CHANNEL_LAST_SET = 1513 # 4-byte uint32 LE; read mirror of 1504
ATTR_SCENES = 400
ATTR_SCHEDULE = 500

# Legacy per-channel write alias (used only by the now-deprecated
# build_set_channel / build_get_channel_targets). Kept so those builders
# still import cleanly. The hub no longer writes per-channel — see
# ATTR_LIVE_CHANNEL_CONTROL below (PR-4).
ATTR_LIVE_CHANNEL_TARGET = ATTR_LIVE_CHANNEL_STATE

# Wire size (in bytes) of an `ATTR_LIVE_CHANNEL_STATE` value. The hot-fix
# changed this from 2 (uint16 LE, per-mille) to 4 (uint32 LE, raw scale).
CHANNEL_STATE_ITEM_LEN = 4

# === PR-4 (2026-06-10): DECODED myAI live-control write path ===============
# Captured from the myAI iOS app via PacketLogger HCI log and verified
# byte-for-byte (CRC included) against our codec. myAI controls the light
# with a SINGLE FSCI SET to attribute 407 carrying ALL 7 channels at once,
# via CHAR_TX_DATA. Full spec in memory [[aiprime-write-protocol-decoded]].
ATTR_LIVE_CHANNEL_CONTROL = 407   # 0x0197 — bulk all-channel control WRITE

# Exact channel order myAI places inside the attr-407 frame. Replicated.
CHANNEL_WRITE_ORDER: tuple[int, ...] = (0x11, 0x13, 0x19, 0x1E, 0x16, 0x10, 0x01)

# Fade/ramp byte in the attr-407 header. myAI used 0x0a (10) for slider
# drags and 0x3c (60) for on/off toggles — treat as a fade duration hint.
RAMP_SLIDER = 0x0A
RAMP_POWER = 0x3C

# Channel value scale on the attr-407 WRITE path is 0..1000 (per-mille),
# DIFFERENT from the 0..20000 read scale on attr 1504. Convert between them.
DEVICE_WRITE_VALUE_MAX = 1000


def device_read_to_write(value_device: int) -> int:
    """Convert a 0..DEVICE_VALUE_MAX read value (attr 1504) to 0..1000 write
    units (attr 407)."""
    if value_device <= 0:
        return 0
    if value_device >= DEVICE_VALUE_MAX:
        return DEVICE_WRITE_VALUE_MAX
    return round(value_device * DEVICE_WRITE_VALUE_MAX / DEVICE_VALUE_MAX)


def percent_to_write(pct: float) -> int:
    """Convert 0..100 percent directly to 0..1000 attr-407 write units."""
    if pct <= 0:
        return 0
    if pct >= USER_VALUE_MAX:
        return DEVICE_WRITE_VALUE_MAX
    return round(pct * DEVICE_WRITE_VALUE_MAX / USER_VALUE_MAX)


# --- Dispatcher signals ---------------------------------------------------
SIGNAL_STATE_UPDATED = "aiprime_ble_state_updated_{entry}"
SIGNAL_AVAILABILITY = "aiprime_ble_availability_{entry}"
