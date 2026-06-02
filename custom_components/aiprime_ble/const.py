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
DEVICE_VALUE_MAX = 20000  # raw device units — full scale
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
#       Fan makes sense (auto temp control). 0x1E being unsettable is the
#       strongest hint yet that 0x1E IS Moonlight — Moonlight is schedule-
#       only on AI Prime, not a directly-targetable channel. Dashboard
#       label-mapping fix-up will likely swap 0x16 ↔ 0x1E for Moonlight vs
#       (one of the white channels).
#
#   1513 (ATTR_LIVE_CHANNEL_LAST_SET):
#     - 4-byte payload, returned identical values to 1504 across all
#       channels. Likely an alias / mirror until someone writes.
#
ATTR_CHANNEL_STATUS_WORD = 1500   # 2-byte status flag; always 0 in current observations
ATTR_LIVE_CHANNEL_STATE = 1504    # 4-byte uint32 LE; this is what the poll reads
ATTR_LIVE_CHANNEL_LAST_SET = 1513 # 4-byte uint32 LE; mirrors LIVE_CHANNEL_STATE
ATTR_SCENES = 400
ATTR_SCHEDULE = 500

# Backward-compat alias for protocol/fsci.py's build_get_channel_targets and
# build_set_channel — PR #10's rebased rename dropped this symbol but fsci.py
# still imports it, causing ImportError at integration load. Both names point
# at the same attribute (1504) which does double duty: reading current state
# and writing target. PR-3c will rename the fsci.py builders + fix the
# stale 2-byte/1000-clamp inside build_set_channel, and at that point this
# alias can be removed.
ATTR_LIVE_CHANNEL_TARGET = ATTR_LIVE_CHANNEL_STATE

# Wire size (in bytes) of an `ATTR_LIVE_CHANNEL_STATE` value. The hot-fix
# changed this from 2 (uint16 LE, per-mille) to 4 (uint32 LE, raw scale).
CHANNEL_STATE_ITEM_LEN = 4

# --- Dispatcher signals ---------------------------------------------------
SIGNAL_STATE_UPDATED = "aiprime_ble_state_updated_{entry}"
SIGNAL_AVAILABILITY = "aiprime_ble_availability_{entry}"
