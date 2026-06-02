# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — PR-3c precursor (2026-06-02): retry-with-backoff in `_send_request`
- **`_send_request` is now a retry wrapper** around a single-attempt
  `_send_request_once`. Three attempts total (1 initial + 2 retries) with
  per-attempt timeouts of `5.0s / 3.0s / 3.0s` and inter-attempt backoffs
  of `0.5s / 1.0s`. Worst-case end-to-end per call: 12.5s.
- **Background.** Post-PSRAM HA log analysis (2026-06-02) showed payload-
  bearing FSCI notifications from AI Prime's Qualcomm QCA4020 still drop
  at ~70% rate through the ESP32-S3 BT proxy's Bluedroid stack — PSRAM
  fixed buffer-contention but not the Qualcomm-vs-Bluedroid fragment
  format mismatch. A single round-trip therefore has ~28% success
  against this specific device + proxy combo. The 3-attempt retry brings
  per-call success to ~63% and compounds per-channel across cycles:
  - 1 poll cycle (30s): ~63%
  - 2 cycles (60s): ~86%
  - 3 cycles (90s): ~94%
- **Each attempt invokes the builder fresh** (callers pass a lambda
  returning `(msg_id, frame)` instead of pre-built args), so a late
  response to a timed-out attempt is dispatched as an unmatched RX
  (DEBUG-logged) rather than shadowing the new request.
- **Stops retrying immediately on BLE disconnect.** The reconnect epoch
  loop will restart the connection and a future poll cycle will
  re-attempt.
- **Log noise reduction.** Intermediate per-attempt failures now log at
  DEBUG; only final exhaustion logs at WARNING. Pre-PR the log would
  fill with 5+ `FSCI request msg_id=N timed out` WARNINGs per poll
  cycle; now only the final "exhausted 3 attempts" warning appears, and
  only for requests that fail all 3 retries.

### Changed — PR-3c precursor (2026-06-02)
- **All four FSCI callers updated to the builder-lambda style:**
  - `_read_fsci_serial`
  - `_read_fsci_firmware`
  - `_async_discover_channels`
  - `_async_read_channel_state`
- **`manifest.json`:** version `0.1.3` → `0.1.4`. Patch bump — additive
  resilience (no behavior change on the happy path; per-channel poll
  cycle takes longer in failure modes but channel values populate
  reliably where they previously stayed at 0).

### Notes — PR-3c precursor
- This is **defense in depth**, not a workaround for a known integration
  bug. BLE round-trips are inherently best-effort even on healthy
  stacks; a retry layer is good hygiene regardless of the proxy/device
  combination in front of the integration.
- Per-channel state poll cycle worst case: 7 channels × 12.5s = 87.5s.
  Cycle interval is 30s; if a cycle runs long the next interval is
  skipped silently (`_poll_lock` already guards). Channels still
  populate within 2-3 cycles in the worst case.
- The retry math depends on independence between attempts. The BT proxy
  fragmentation pattern observed empirically appears semi-stochastic
  (different msg_ids fail per cycle), so the independence assumption is
  approximately valid. If a future BT proxy fix removes the ~70% drop
  rate, the retry layer becomes near-no-op (one attempt almost always
  succeeds) — no need to undo this change.
- PR-3c itself (the first real mutating write `async_set_channel` via
  `build_set_channel`) still pending. With retry now in place, mutating
  writes will also benefit from the same resilience.

### Fixed — hot-fix #2 (2026-06-02, post-merge regression)
- **Restore `ATTR_LIVE_CHANNEL_TARGET` symbol.** PR #10's rebased rename of
  the `1504` attribute constant (to `ATTR_LIVE_CHANNEL_STATE`) silently
  dropped `ATTR_LIVE_CHANNEL_TARGET`, but `protocol/fsci.py` still imports
  that name in `build_get_channel_targets` and `build_set_channel`. The
  resulting `ImportError` at module load made the integration fail to set
  up, and HA marked every `aiprime_ble` entity as "no longer being
  provided by the integration" — Master Light, all 6 channel number
  entities, BLE RSSI, Fan, Firmware version, and the 5 device-info
  sensors all went unavailable simultaneously.

  Restored as a backward-compat alias: `ATTR_LIVE_CHANNEL_TARGET =
  ATTR_LIVE_CHANNEL_STATE`. Both names point at the same attribute (1504)
  which empirically does double duty (the probe confirmed reads return
  the live target). PR-3c will rename the `fsci.py` builders and fix the
  stale 2-byte / `1000`-clamp inside `build_set_channel` in one pass; at
  that point this alias can be removed.

- **`manifest.json`:** version `0.1.2` → `0.1.3`. Patch bump — regression fix.

### Notes — hot-fix #2
- Detected via `search_code` for `ATTR_LIVE_CHANNEL_TARGET` across the
  repo immediately after the user reported all entities unavailable
  post-merge. The lesson here matches `[[config-file-full-rewrite-trap]]`
  — symbol renames in shared modules require a grep pass before merge.
  A pre-merge CI lint that runs `python -c "import custom_components.aiprime_ble"`
  would have caught this; tracking as a Day-N+1 housekeeping item.

### Fixed — hot-fix (2026-06-02, rebased)
- **Channel-state poll now reports real brightness values.** PR-3a's poll
  was reading attribute `1500` (placeholder, marked "best guess from dump
  — confirm"), which empirically returns a 2-byte status word always
  equal to `0x0000` regardless of what the LED driver is doing. The
  dashboard tiles consequently showed 0% on every channel even when the
  light was visibly on.

  `aiprime_channel_probe.py` (standalone bleak probe) queried all three
  candidate attributes (`1500` / `1504` / `1513`) against all 7 discovered
  channel IDs and confirmed:

  - `1500` returns 2-byte `0x0000` for every channel → renamed to
    `ATTR_CHANNEL_STATUS_WORD`; no longer used by the poll.
  - `1504` returns 4-byte uint32 LE in `0..~20000` that matches what the
    LED driver is actually outputting → this is now `ATTR_LIVE_CHANNEL_STATE`.
  - `1513` mirrors `1504` (likely an alias until someone writes).
  - `0x01` (fan) and `0x1E` return `InvalidElement` on `1504` — both are
    system-managed, not user-targetable. Fan makes sense; `0x1E` being
    unsettable strongly hints that `0x1E` IS Moonlight (schedule-only)
    rather than the Cool White the heuristic dashboard labels assumed.

- **Value scale `DEVICE_VALUE_MAX` changed `1000` → `20000`.** Reflects
  the actual wire range. `percent_to_device` / `device_to_percent` updated
  to match. State storage interpretation: `state.channels[cid].value_device`
  now holds raw device units (0..20000) rather than per-mille (0..1000).
- **`_async_read_channel_state` reads 4 bytes as uint32 LE** (was 2 bytes
  as uint16 LE). Item-length check raised from `len(raw) < 2` to
  `len(raw) < CHANNEL_STATE_ITEM_LEN` (=4).
- **`manifest.json`:** version `0.1.1` → `0.1.2`. Patch bump — bug fix.
  Originally proposed in PR #9 as `0.1.0 → 0.1.1`, but PR-3b merged first
  and took `0.1.1`, so this rebased hot-fix lands on top of `0.1.1` and
  bumps to `0.1.2`.

### Notes — hot-fix
- The probe also surfaced the likely Moonlight ID confusion (`0x1E`
  unsettable suggests Moonlight, not Cool White). The dashboard label
  fix-up in `home-assistant-config` should swap `0x16 → Moonlight` for
  `0x1E → Moonlight` (and reassign `0x16` to one of the other colors)
  in a follow-up. Not done here to keep the hot-fix narrowly scoped to
  the integration repo.

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
- Real `async_set_channel` via lifted FSCI codec. Now that the value scale is
  known (`0..20000`), the write path will use `percent_to_device` to translate
  user-facing 0-100% into the right raw value.
- Post-write re-poll to confirm device state.
- Verify `instance=channel_id` is correct vs. positional indexing.
- Also: rename `fsci.py`'s `build_get_channel_targets` → `build_get_channel_state_all`
  (or similar), drop the `ATTR_LIVE_CHANNEL_TARGET` alias added in hot-fix #2,
  and fix the stale 2-byte / 1000-clamp inside `build_set_channel` to use
  `CHANNEL_STATE_ITEM_LEN` + `DEVICE_VALUE_MAX`. The retry wrapper added in
  this PR will automatically apply to write CONFIRMs too — important because
  write CONFIRMs are payload-bearing and would otherwise face the same drop
  rate as channel-state reads.

### Pending (Day 5 — per-channel sliders + channel-name discovery)
- `number.py` per-channel sliders connected to `async_set_channel`.
- Empirical channel-to-color discovery flow at first install (uses captured
  Mobius app channel map as seed: Blue / Green / Deep Red / Moonlight /
  Warm White / Cool White).

## [0.0.1] — 2026-05-29

- Repository scaffolded.
