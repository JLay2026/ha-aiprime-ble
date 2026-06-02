# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Day 4 PR-3a (2026-06-02)
- **Device-side channel discovery.** `_async_discover_channels()` GETs
  `ATTR_CHANNEL_LIST` (901) at connect and rebuilds `state.channels` from the
  device's canonical list. Replaces the hardcoded `ALL_CHANNEL_IDS` tuple at
  runtime, so channels not in our defaults (e.g. the suspected `0x07` Moonlight
  slot, plus anything the Reef variant might add) start working as soon as
  they're advertised by the device. Unknown channel IDs get auto-labels like
  `Channel 0x07`; smoke-test results inform a subsequent CHANNEL_DEFAULT_LABELS
  fix-up.
- **Read-only periodic state poll.** Per-channel
  `GET ATTR_LIVE_CHANNEL_STATE(channel_id)` runs immediately at connect and
  every `DEFAULT_STATE_POLL_INTERVAL_S` (30s) afterwards via
  `async_track_time_interval`. Lock-guarded so polls don't overlap; skipped
  silently when disconnected. One round-trip per channel keeps parsing
  simple (single-instance responses).
- **Discovery diagnostics.** Raw `ATTR_CHANNEL_LIST` reply hex + parsed entry
  hex are logged at DEBUG so we can verify the response format on first
  smoke-test and adjust parsing if the device packs multiple bytes per entry.
- **Connect log line extended** with `channels=0x01, 0x10, 0x11, …` listing
  the discovered channel IDs in sorted order.

### Notes — Day 4 PR-3a
- No mutating writes — `async_set_channel` / `async_set_power` remain stubs.
  PR-3c implements the first real write.
- If `ATTR_CHANNEL_LIST` returns nothing (timeout, malformed, or empty), the
  hub keeps the hardcoded defaults from `_initialize_channels` rather than
  going silent. WARNING-level log makes this visible.
- Channel-list parsing assumes one byte per entry. If MOBIUS packs (id +
  metadata) bytes per entry, the DEBUG hex dump will reveal it and we
  adjust in a fix-up.

### Pending (Day 4 PR-3b — FSCI firmware read)
- `_read_fsci_firmware()` to populate `state.firmware` from
  `ATTR_FIRMWARE_VERSION (11)`.

### Pending (Day 4 PR-3c — first mutating write)
- Real `async_set_channel` via lifted FSCI codec.
- Post-write re-poll to confirm device state.
- Verify `instance=channel_id` is correct vs. positional indexing.

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

### Pending (Day 5 — per-channel sliders + channel-name discovery)
- `number.py` per-channel sliders connected to `async_set_channel`.
- Empirical channel-to-color discovery flow at first install (uses captured
  Mobius app channel map as seed: Blue / Green / Deep Red / Moonlight /
  Warm White / Cool White).

## [0.0.1] — 2026-05-29

- Repository scaffolded.
