# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

### Pending (Day 4 PR-2 — BLE connect + TX/RX wiring)
- `aiprime_hub.py` real BLE connection lifecycle using HA's bluetooth
  integration + `bleak-retry-connector`.
- TX queue + RX dispatch (subscribe to RX_DATA/RX_FINAL, route CONFIRMs by msg_id).
- Wire `read_device_info()` into `async_setup()` to populate the new 0x180A
  sensors.

### Pending (Day 4 PR-3 — first mutating write)
- Real `async_set_channel` via lifted FSCI codec.
- Periodic state poll every 30s.

### Pending (Day 5 — per-channel sliders + channel-name discovery)
- `number.py` per-channel sliders connected to `async_set_channel`.
- Empirical channel-to-color discovery flow at first install (uses captured
  Mobius app channel map as seed: Blue / Green / Deep Red / Moonlight /
  Warm White / Cool White).

## [0.0.1] — 2026-05-29

- Repository scaffolded.
