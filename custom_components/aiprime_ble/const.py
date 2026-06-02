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
# 6 LED channels + 1 fan, value scale 0-1000 (per-mille).
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
DEVICE_VALUE_MAX = 1000  # per-mille — the wire value range
USER_VALUE_MAX = 100     # percent — what HA Number entities expose


def percent_to_device(pct: float) -> int:
    """Convert 0-100 user percent into 0-1000 device per-mille."""
    if pct <= 0:
        return 0
    if pct >= USER_VALUE_MAX:
        return DEVICE_VALUE_MAX
    return round(pct * DEVICE_VALUE_MAX / USER_VALUE_MAX)


def device_to_percent(value: int) -> float:
    """Convert 0-1000 device per-mille into 0-100 user percent."""
    if value <= 0:
        return 0.0
    if value >= DEVICE_VALUE_MAX:
        return float(USER_VALUE_MAX)
    return value * USER_VALUE_MAX / DEVICE_VALUE_MAX


# --- Known FSCI attribute IDs (read-only metadata) ------------------------
ATTR_SERIAL = 3              # ASCII serial string (validated Day 3: "A09F0AE2D0R1CF")
ATTR_FIRMWARE_VERSION = 11   # placeholder; verify in PR-2 smoke test
ATTR_TIMEZONE_POSIX = 205
ATTR_TIMEZONE_NAME = 206
ATTR_MESH_LOCAL_ADDRESSES = 1005
ATTR_BLE_MAC = 1603
ATTR_CHANNEL_LIST = 901

# Live state / writable channel attributes (best guess from dump — confirm)
ATTR_LIVE_CHANNEL_STATE = 1500
ATTR_LIVE_CHANNEL_TARGET = 1504
ATTR_LIVE_CHANNEL_LAST_SET = 1513
ATTR_SCENES = 400
ATTR_SCHEDULE = 500

# --- Dispatcher signals ---------------------------------------------------
SIGNAL_STATE_UPDATED = "aiprime_ble_state_updated_{entry}"
SIGNAL_AVAILABILITY = "aiprime_ble_availability_{entry}"
