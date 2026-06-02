# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Day 4 PR-2 (2026-06-02)
- **Real BLE connection lifecycle** in `aiprime_hub.py`:
  - HA `bluetooth` integration resolves `BLEDevice` from the configured MAC.
  - `bleak-retry-connector` `establish_connection` opens + maintains the GATT
    link with `BleakClientWithServiceCache` for fast reconnect.
  - `close_stale_connections_by_address` runs first to clear any lingering
    bleak state.
  - Connection runs in a background task — `async_setup_entry` does not block
    on BLE, so HA boots cleanly even when MOBIUS is briefly out of range.
- **RX dispatch.** Subscribes to `CHAR_RX_DATA` + `CHAR_RX_FINAL` on connect;
  `RX_FINAL` flushes the accumulated buffer, extracts the msg_id from bytes
  3-4, and resolves the matching in-flight `asyncio.Future`.
- **TX path.** `_send_request(msg_id, frame)` registers the future, writes to
  `CHAR_TX_DATA` without response, awaits CONFIRM with a 5s timeout. Disconnect
  cancels all in-flight futures.
- **Post-connect smoke test.** Round-trips `GET ATTR_SERIAL(3)` (the Day 3
  validation query). Populates `state.serial`. If the round-trip fails the
  connection still stays up — the failure is logged at WARNING for diagnostic,
  and the 0x180A read still runs.
- **0x180A Device Info wiring.** `protocol.device_info.read_device_info()` is
  called post-connect; populates all six `DeviceState` metadata fields. The
  5 sensors added in PR-1 finally have data.
- **Passive RSSI tracking** via `bluetooth.async_register_callback` with
  `PASSIVE` scanning + `connectable=False`. Works even when the GATT link is
  down — the BLE signal strength sensor stays meaningful through reconnect
  attempts.
- **Reconnect with exponential backoff.** Initial 1s, doubles each attempt,
  capped at 30s. Disconnect callback from bleak triggers a reschedule unless
  `async_unload` has marked the disconnect intentional.

### Changed — Day 4 PR-2
- **`manifest.json`:** declares `bleak-retry-connector>=3.4.0` requirement;
  bumps version to `0.0.2`.

### Notes — Day 4 PR-2
- `async_set_channel` / `async_set_power` remain stubs — those are PR-3 (first
  mutating write). Shipping writes without the periodic state poll that PR-3
  introduces would risk silently driving the light to an unexpected state.
- The existing `Firmware version` sensor (FSCI `ATTR_FIRMWARE_VERSION`) still
  shows as unavailable — verifying that attribute returns sensible data is part
  of PR-3's smoke test.

### Added — Day 4 PR-1 (2026-06-02)
- **FSCI protocol codec** (`protocol/fsci.py`): full frame builder lifted from
  [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32)
  after Day 3 validation confirmed bit-identical wire format on the Qualcomm
  QCA4020. Includes `FsciCodec` class with rotating message ID counter and
  builders for `build_get_attribute`, `build_set_attribute`, `build_handshake`,
  `build_get_channel_state`, `build_get_channel_targets`, `build_set_channel`.
  CRC16-CCITT (poly 0x1021, init 0xFFFF). Pure module — no BLE I/O.
- **Standard 0x180A Device Information reader** (`protocol/device_info.py`):
  best-effort async reader for manufacturer, model, serial, hardware/firmware/
  software revisions. Each char read independently — missing chars yield None
  rather than aborting.
- **5 new diagnostic sensors** (`sensor.py`): manufacturer, model number,
  serial number (DI-side), hardware revision, software revision. All show as
  unavailable until PR-2 wires the BLE connection — same status as the
  existing firmware sensor.
- **`DeviceState` metadata fields** (`types.py`): `manufacturer`,
  `model_number`, `serial_number`, `hardware_revision`, `firmware_revision`,
  `software_revision`. Distinct from the existing `firmware` and `serial`
  fields which come from FSCI attributes (the DI-side values may differ).
- **0x180A char UUID constants** + `SERVICE_DEVICE_INFO` in `const.py`.
- **`CHAR_AUX = 01ff0105-…`** constant for the 5th proprietary GATT char
  discovered during Day 3 validation. Purpose TBD; held for future PRs.

### Pending (Day 4 PR-3 — first mutating write)
- Real `async_set_channel` via lifted FSCI codec.
- Periodic state poll every 30s via `build_get_channel_state`.
- Verify `ATTR_FIRMWARE_VERSION` returns parseable data, populate `state.firmware`.

### Pending (Day 5 — per-channel sliders + channel-name discovery)
- `number.py` per-channel sliders connected to `async_set_channel`.
- Empirical channel-to-color discovery flow at first install (uses captured
  Mobius app channel map as seed: Blue / Green / Deep Red / Moonlight /
  Warm White / Cool White).

## [0.0.1] — 2026-05-29

- Repository scaffolded.
