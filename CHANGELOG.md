# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Day 4 PR-3b (2026-06-02)
- **FSCI firmware sensor.** `_read_fsci_firmware()` rounds-trip
  `GET ATTR_FIRMWARE_VERSION (11)` at connect and populates
  `state.firmware` — lights up the existing `Firmware version` sensor
  that was permanently `unavailable` until now. Decode strategy is
  best-effort with three fallbacks (printable ASCII → packed version
  bytes joined with `.` → hex string for diagnostic visibility). Raw
  payload is logged at DEBUG either way.

### Changed — Day 4 PR-3b
- **`Model number` sensor renamed to `Build version`.** AI populates the
  standard 0x180A 2A24 (Model Number) characteristic with what looks
  like a version string (PR-3a smoke test showed `4.2.1.1`) rather than
  a model name. The new label is honest about the actual content.
  Entity_id and unique_id are preserved — dashboards and automations
  keyed on the entity_id continue to work unchanged.
- **Connect-failure log noise reduced.** The first 5 `_async_connect`
  attempts now log at DEBUG instead of WARNING. The HA `bluetooth`
  integration typically needs ~5-25s after `aiprime_ble` loads to
  populate its device cache, and the noisy `BLE device not found in
  cache; will retry` stream during that window isn't actionable.
  Sustained failures (after attempt 6 ≈ ~60s of backoff) still escalate
  to WARNING. Disconnect callback resets the attempt counter so a
  reconnect epoch also gets the quiet window. PR-2 smoke test surfaced
  this issue.
- **`manifest.json`:** version `0.1.0` → `0.1.1`. Patch bump — additive
  feature (firmware sensor) + polish (log noise, sensor label).
- **Connect INFO log extended** to show both firmware sources side by
  side: `... build=4.2.1.1 fw_di=1.0 fw_fsci=<FSCI value> ...`.

### Notes — Day 4 PR-3b
- No mutating writes — `async_set_channel` / `async_set_power` remain
  stubs. PR-3c implements the first real write.
- If `ATTR_FIRMWARE_VERSION` returns nothing or fails, the existing
  `Firmware version` sensor stays `unavailable` — no behavior change
  from before.

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
- Channel-list parsing assumes one byte per entry. PR-3a smoke test (logged
  2026-06-02) confirmed this assumption holds for AI Prime 16 Freshwater —
  device returned exactly the 7 hardcoded IDs `0x01, 0x10, 0x11, 0x13, 0x16,
  0x19, 0x1E` (disproving the earlier "channel 7 might be Moonlight"
  hypothesis).

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

### Pending (Day 4 PR-3c — first mutating write)
- Real `async_set_channel` via lifted FSCI codec.
- Post-write re-poll to confirm device state.
- Verify `instance=channel_id` is correct vs. positional indexing.

### Pending (Day 5 — per-channel sliders + channel-name discovery)
- `number.py` per-channel sliders connected to `async_set_channel`.
- Empirical channel-to-color discovery flow at first install (uses captured
  Mobius app channel map as seed: Blue / Green / Deep Red / Moonlight /
  Warm White / Cool White).

## [0.0.1] — 2026-05-29

- Repository scaffolded.
