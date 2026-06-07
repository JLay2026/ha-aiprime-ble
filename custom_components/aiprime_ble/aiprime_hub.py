"""Central hub: owns the BLE connection, holds device state, routes writes.

PR-2 (2026-06-02) — replaces the v0.0.1 stubs with a real BLE connection
lifecycle (connect/disconnect, RX dispatch, post-connect ATTR_SERIAL + 0x180A
reads, passive RSSI tracking, reconnect loop with exponential backoff).

PR-3a (2026-06-02) — adds READ-ONLY periodic state poll:
  - Channel-list DISCOVERY at connect via GET ATTR_CHANNEL_LIST (901).
  - Per-channel GET ATTR_LIVE_CHANNEL_STATE(channel_id) every 30s.

PR-3b (2026-06-02) — small read + polish:
  - FSCI firmware read via GET ATTR_FIRMWARE_VERSION (11), populates
    state.firmware (lights up the existing Firmware sensor).
  - First 5 connect attempts log at DEBUG instead of WARNING.

Hot-fix (2026-06-02, rebased onto post-PR-3b main) — channel-state poll
now reads CHANNEL_STATE_ITEM_LEN (=4) bytes as uint32 LE at attribute 1504
(was 2 bytes at 1500 — that's a status word, not brightness).

Retry-with-backoff (2026-06-02, PR-3c precursor) — `_send_request` is now a
retry wrapper around a single-attempt `_send_request_once`. Background:
the ESP32-S3 BT proxy's Bluedroid stack drops payload-bearing notification
fragments from AI Prime's Qualcomm QCA4020 controller at a ~70% rate even
after PSRAM enablement (see [[aiprime-bt-proxy-acl-fragmentation]] memory).
A single FSCI round-trip therefore has ~28% success against this device.
With 3 attempts at 5s/3s/3s timeouts and 0.5s/1.0s backoffs:
  - Per-call success: 1 - 0.72^3 ≈ 63%
  - Per-channel success across 2 poll cycles: 1 - 0.37^2 ≈ 86%
  - Per-channel success across 3 cycles: 94%
Each attempt uses a FRESH msg_id (builder is invoked per attempt) so a
late response to a timed-out attempt doesn't shadow a fresh request.

PR-3c (2026-06-06) — first mutating writes:
  - `async_set_channel` builds a real FSCI SET via codec.build_set_channel,
    sends through _send_request retry wrapper, applies optimistic state
    update with rollback on failure.
  - `async_set_power` walks LED channels (skipping fan + known-unwritable
    0x1E) and calls async_set_channel for each. "On" restores the last-
    known nonzero value per channel, falling back to 50% if unknown.
  - Periodic poll opportunistically populates _last_nonzero_values so
    HA-initiated on/off restores values set by Mobius / schedule too.
  - Known-unwritable channels (CHANNEL_ID_FAN, CHANNEL_ID_LIKELY_MOONLIGHT)
    are silently skipped at DEBUG level — they return InvalidElement on
    attribute 1504 by design (fan is auto-managed, 0x1E is schedule-only).

PR-3e (2026-06-07) — write-path experiment via CHAR_AUX:
  PR-3c and PR-3d both deployed real writes — PR-3c to attribute 1504 via
  CHAR_TX_DATA, PR-3d to attribute 1513 via CHAR_TX_DATA. Both produced
  status=SUCCESS at the FSCI level (~100ms round-trip, no retries needed)
  but the AI Prime never physically applied any value. Schedule-conflict
  hypothesis disproven: user deleted all schedule points via myAI, ran
  manual-on for 1 min via myAI, swipe-killed myAI, re-tested via HA — no
  physical change. Both attribute targets are silent-ACK-and-discard.

  PR-3e tests a different hypothesis: maybe writes need to go to CHAR_AUX
  (01ff0105) instead of CHAR_TX_DATA. CHAR_AUX was discovered Day 3 but
  never used — per const.py "[write-without-response, notify]. Purpose
  TBD — candidates: bulk-write streaming, push notifications, OTA fast
  path." If myAI actually uses CHAR_AUX for direct control while
  CHAR_TX_DATA is for config queries, this swap should make writes
  physically apply.

  Changes:
    - `_send_request_once` now writes to CHAR_AUX (was CHAR_TX_DATA).
    - `_async_connect` ALSO subscribes to CHAR_AUX notifications in case
      responses come back there instead of (or in addition to)
      CHAR_RX_DATA / CHAR_RX_FINAL. AUX subscription failure is
      tolerated — connection continues with just the existing two
      subscriptions, and writes-via-AUX may still work if the device
      sends responses back on CHAR_RX_FINAL.
    - All AUX notifications route to the same _on_rx_final handler so the
      msg_id matcher and in-flight future dict pick them up automatically.

  If THIS doesn't work either, the next escalation is a BLE sniff of myAI
  (~$30-50 nRF52840 dongle + Wireshark) to capture ground-truth wire
  protocol for direct control. Cheaper attribute-probing approaches
  exhausted at that point.

Reconnect topology: one long-running `_connect_with_retry` task per
"connection epoch". The task loops with exponential backoff until a connect
succeeds, then exits. The bleak disconnect callback spawns a fresh task
when the link drops — there is at most one connect task in flight at any
time, guarded by `_connect_task` and the per-attempt `_connect_lock`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    close_stale_connections_by_address,
    establish_connection,
)

from .const import (
    ALL_CHANNEL_IDS,
    ATTR_CHANNEL_LIST,
    ATTR_FIRMWARE_VERSION,
    ATTR_LIVE_CHANNEL_STATE,
    ATTR_SERIAL,
    CHANNEL_DEFAULT_LABELS,
    CHANNEL_ID_FAN,
    CHANNEL_STATE_ITEM_LEN,
    CHAR_AUX,
    CHAR_RX_DATA,
    CHAR_RX_FINAL,
    CHAR_TX_DATA,
    CONF_ADDRESS,
    CONF_NAME,
    DEFAULT_RECONNECT_BACKOFF_CAP_S,
    DEFAULT_RECONNECT_BACKOFF_INITIAL_S,
    DEFAULT_STATE_POLL_INTERVAL_S,
    DEVICE_VALUE_MAX,
    DOMAIN,
    SIGNAL_AVAILABILITY,
    SIGNAL_STATE_UPDATED,
)
from .protocol import (
    FsciCodec,
    STATUS_SUCCESS,
    parse_get_attribute_payload,
    parse_response_status,
    read_device_info,
    status_name,
    to_hex,
)
from .types import ChannelState, DeviceState

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT_S = 5.0
_RETRY_TIMEOUT_S = 3.0
_RETRY_BACKOFFS_S: tuple[float, ...] = (0.5, 1.0)
_DEFAULT_RETRIES = 2
_QUIET_CONNECT_ATTEMPTS = 5

# PR-3c (2026-06-06): channels that are NOT user-writable per the 2026-06-02
# channel-state probe (see const.py "Channel state attributes" comment).
# Writes to these channels return InvalidElement on attribute 1504 by design:
#   - 0x01 (fan): auto-managed by AI Prime's internal temperature control
#   - 0x1E: empirically returns InvalidElement on writes; likely Moonlight,
#     which is schedule-only on the AI Prime platform (not directly settable).
# We silently skip writes to these channels rather than emit error noise.
# When channel labeling is empirically corrected (future PR), reconsider
# whether 0x1E should be filtered out at the entity-builder layer instead.
_CHANNEL_ID_LIKELY_MOONLIGHT = 0x1E
_UNWRITABLE_CHANNEL_IDS: frozenset[int] = frozenset(
    {CHANNEL_ID_FAN, _CHANNEL_ID_LIKELY_MOONLIGHT}
)

# Fallback brightness for "Master ON" when we have no last-known value for
# a channel (e.g., immediately after HA boot before the first periodic poll
# captures the device's current state). 50% of full scale.
_POWER_ON_FALLBACK_DEVICE_VALUE = DEVICE_VALUE_MAX // 2

# PR-3e (2026-06-07): characteristic used for FSCI request writes.
# CHAR_TX_DATA (01ff0103) was the original target since PR-2; PR-3c and
# PR-3d both wrote there with status=SUCCESS but no physical effect.
# CHAR_AUX (01ff0105) is the experimental target — discovered Day 3,
# `[write-without-response, notify]`, never used until now.
# Swap this constant back to CHAR_TX_DATA to revert PR-3e if needed.
_WRITE_CHARACTERISTIC = CHAR_AUX

_FrameBuilder = Callable[[], "tuple[int, bytes]"]


class AIPrimeHub:
    """Owns the BLE session and the in-memory device state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data.get(CONF_ADDRESS, "")
        self.name: str = entry.data.get(CONF_NAME, "AI Prime")
        self.state = DeviceState(address=self.address, name=self.name)
        self._initialize_channels()

        self._codec = FsciCodec()
        self._client: BleakClientWithServiceCache | None = None
        self._rx_buffer = bytearray()
        self._in_flight: dict[int, asyncio.Future[bytes]] = {}

        self._connect_lock = asyncio.Lock()
        self._connect_task: asyncio.Task[None] | None = None
        self._reconnect_backoff: float = DEFAULT_RECONNECT_BACKOFF_INITIAL_S
        self._intentional_disconnect: bool = False
        self._connect_attempt: int = 0

        self._poll_lock = asyncio.Lock()

        # PR-3c: track last-seen nonzero value per channel for "Master ON"
        # restore. Populated by writes AND by periodic polls (so values set
        # via Mobius / device schedule are also remembered).
        self._last_nonzero_values: dict[int, int] = {}

    async def async_setup(self) -> None:
        if not self.address:
            _LOGGER.error("AIPrimeHub setup: no CONF_ADDRESS in entry data")
            return

        unsub_rssi = bluetooth.async_register_callback(
            self.hass,
            self._handle_advertisement,
            BluetoothCallbackMatcher(address=self.address, connectable=False),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        self.entry.async_on_unload(unsub_rssi)

        unsub_poll = async_track_time_interval(
            self.hass,
            self._async_poll_state_callback,
            timedelta(seconds=DEFAULT_STATE_POLL_INTERVAL_S),
        )
        self.entry.async_on_unload(unsub_poll)

        self._spawn_connect_task()

    async def async_unload(self) -> None:
        self._intentional_disconnect = True
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._async_disconnect()

    def _spawn_connect_task(self) -> None:
        if self._intentional_disconnect:
            return
        if self._connect_task and not self._connect_task.done():
            return
        self._connect_task = self.hass.async_create_task(self._connect_with_retry())

    async def _connect_with_retry(self) -> None:
        attempt = 0
        while not self._intentional_disconnect:
            attempt += 1
            self._connect_attempt = attempt
            try:
                await self._async_connect()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "AIPrimeHub %s: connect attempt %d errored: %s",
                    self.address, attempt, err,
                )

            if self.state.ble_connected:
                return
            if self._intentional_disconnect:
                return

            delay = self._reconnect_backoff
            self._reconnect_backoff = min(delay * 2, DEFAULT_RECONNECT_BACKOFF_CAP_S)
            _LOGGER.debug(
                "AIPrimeHub %s: reconnect attempt %d in %.1fs",
                self.address, attempt + 1, delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    def _attempt_log_level(self) -> int:
        if self._connect_attempt <= _QUIET_CONNECT_ATTEMPTS:
            return logging.DEBUG
        return logging.WARNING

    async def _async_connect(self) -> None:
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                return
            if self._intentional_disconnect:
                return

            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if ble_device is None:
                _LOGGER.log(
                    self._attempt_log_level(),
                    "AIPrimeHub %s: BLE device not found in HA bluetooth "
                    "cache; will retry (attempt %d)",
                    self.address, self._connect_attempt,
                )
                return

            await close_stale_connections_by_address(self.address)

            try:
                client = await establish_connection(
                    client_class=BleakClientWithServiceCache,
                    device=ble_device,
                    name=self.name,
                    disconnected_callback=self._handle_disconnected,
                    max_attempts=3,
                )
            except BLEAK_RETRY_EXCEPTIONS as err:
                _LOGGER.log(
                    self._attempt_log_level(),
                    "AIPrimeHub %s: connect failed (attempt %d): %s",
                    self.address, self._connect_attempt, err,
                )
                return

            self._client = client
            self._rx_buffer.clear()

            try:
                await client.start_notify(CHAR_RX_DATA, self._on_rx_data)
                await client.start_notify(CHAR_RX_FINAL, self._on_rx_final)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "AIPrimeHub %s: RX subscribe failed: %s", self.address, err
                )
                await self._async_disconnect()
                return

            # PR-3e (2026-06-07): also subscribe to CHAR_AUX notify in case
            # writes-via-AUX get responses back on AUX instead of (or in
            # addition to) CHAR_RX_DATA/CHAR_RX_FINAL. AUX subscription is
            # best-effort — if it fails, log and continue. Writes may still
            # succeed via the existing two RX subscriptions.
            try:
                await client.start_notify(CHAR_AUX, self._on_rx_final)
                _LOGGER.debug(
                    "AIPrimeHub %s: CHAR_AUX notify subscribed (PR-3e)",
                    self.address,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "AIPrimeHub %s: CHAR_AUX notify subscribe failed "
                    "(continuing without it): %s", self.address, err,
                )

            self.state.ble_connected = True
            self._reconnect_backoff = DEFAULT_RECONNECT_BACKOFF_INITIAL_S

            await self._read_fsci_serial()
            await self._read_fsci_firmware()
            await self._async_discover_channels()
            await self._async_read_channel_state()
            await self._read_device_info(client)

            self._notify_availability_changed()
            self._notify_state_changed()
            _LOGGER.info(
                "AIPrimeHub %s: connected; serial=%s manufacturer=%s "
                "build=%s fw_di=%s fw_fsci=%s channels=%s",
                self.address,
                self.state.serial,
                self.state.manufacturer,
                self.state.model_number,
                self.state.firmware_revision,
                self.state.firmware,
                ", ".join(f"0x{cid:02X}" for cid in sorted(self.state.channels)),
            )

    async def _async_disconnect(self) -> None:
        client = self._client
        self._client = None
        was_connected = self.state.ble_connected
        self.state.ble_connected = False
        for future in list(self._in_flight.values()):
            if not future.done():
                future.cancel()
        self._in_flight.clear()
        if client:
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "AIPrimeHub %s: disconnect error (ignored): %s",
                    self.address, err,
                )
        if was_connected:
            self._notify_availability_changed()

    def _handle_disconnected(self, _client: Any) -> None:
        _LOGGER.debug("AIPrimeHub %s: bleak disconnected callback", self.address)
        self.state.ble_connected = False
        for future in list(self._in_flight.values()):
            if not future.done():
                future.cancel()
        self._in_flight.clear()
        self._notify_availability_changed()
        if not self._intentional_disconnect:
            self._connect_attempt = 0
            self._reconnect_backoff = DEFAULT_RECONNECT_BACKOFF_INITIAL_S
            self._spawn_connect_task()

    def _on_rx_data(self, _ch: Any, data: bytearray) -> None:
        self._rx_buffer.extend(data)

    def _on_rx_final(self, _ch: Any, data: bytearray) -> None:
        self._rx_buffer.extend(data)
        frame = bytes(self._rx_buffer)
        self._rx_buffer.clear()
        if len(frame) < 5:
            _LOGGER.debug(
                "AIPrimeHub %s: RX frame too short to dispatch: %s",
                self.address, to_hex(frame),
            )
            return
        msg_id = frame[3] | (frame[4] << 8)
        future = self._in_flight.get(msg_id)
        if future is None or future.done():
            _LOGGER.debug(
                "AIPrimeHub %s: unmatched RX msg_id=%d: %s (likely late "
                "reply to a timed-out attempt; safe to ignore)",
                self.address, msg_id, to_hex(frame),
            )
            return
        future.set_result(frame)

    async def _send_request(
        self,
        builder: _FrameBuilder,
        *,
        retries: int = _DEFAULT_RETRIES,
        first_timeout: float = _REQUEST_TIMEOUT_S,
        retry_timeout: float = _RETRY_TIMEOUT_S,
    ) -> bytes | None:
        """Send an FSCI request with retry-with-backoff on timeout.

        BT proxy ACL fragmentation drops ~70% of payload-bearing
        notifications even after PSRAM enablement (see
        [[aiprime-bt-proxy-acl-fragmentation]]). Three attempts with
        0.5s/1.0s backoffs turn ~30% per-attempt success into ~63%
        per-call success, compounding to ~94% across 3 poll cycles.

        Each attempt invokes `builder()` fresh so a late response to a
        timed-out attempt is discarded (logged as unmatched RX) rather
        than shadowing the new request.

        Stops retrying immediately on BLE disconnect — the reconnect
        loop will restart the connection epoch and a future poll cycle
        will re-attempt.
        """
        last_msg_id = -1
        for attempt in range(retries + 1):
            msg_id, frame = builder()
            last_msg_id = msg_id
            timeout = first_timeout if attempt == 0 else retry_timeout
            reply = await self._send_request_once(msg_id, frame, timeout)
            if reply is not None:
                if attempt > 0:
                    _LOGGER.debug(
                        "AIPrimeHub %s: FSCI request succeeded on attempt "
                        "%d/%d (msg_id=%d)",
                        self.address, attempt + 1, retries + 1, msg_id,
                    )
                return reply
            if attempt >= retries:
                break
            if not self.state.ble_connected:
                _LOGGER.debug(
                    "AIPrimeHub %s: FSCI request msg_id=%d skipping "
                    "remaining %d retries (disconnected)",
                    self.address, msg_id, retries - attempt,
                )
                break
            backoff = _RETRY_BACKOFFS_S[min(attempt, len(_RETRY_BACKOFFS_S) - 1)]
            await asyncio.sleep(backoff)
        _LOGGER.warning(
            "AIPrimeHub %s: FSCI request exhausted %d attempts "
            "(last msg_id=%d)",
            self.address, retries + 1, last_msg_id,
        )
        return None

    async def _send_request_once(
        self, msg_id: int, frame: bytes, timeout: float
    ) -> bytes | None:
        """Single-attempt FSCI round-trip. Caller (`_send_request`) handles retries.

        PR-3e (2026-06-07): writes go to _WRITE_CHARACTERISTIC (currently
        CHAR_AUX = 01ff0105). Was CHAR_TX_DATA (01ff0103) since PR-2 —
        switched after PR-3c/3d demonstrated that writes to CHAR_TX_DATA
        ACK with status=SUCCESS but never physically apply. Revert by
        flipping _WRITE_CHARACTERISTIC back to CHAR_TX_DATA if AUX also
        fails or causes regressions.
        """
        client = self._client
        if client is None or not client.is_connected:
            _LOGGER.debug(
                "AIPrimeHub %s: send_request called while disconnected",
                self.address,
            )
            return None

        future: asyncio.Future[bytes] = self.hass.loop.create_future()
        self._in_flight[msg_id] = future
        try:
            await client.write_gatt_char(_WRITE_CHARACTERISTIC, frame, response=False)
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "AIPrimeHub %s: FSCI request msg_id=%d timed out after %.1fs",
                self.address, msg_id, timeout,
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "AIPrimeHub %s: FSCI request msg_id=%d failed: %s",
                self.address, msg_id, err,
            )
            return None
        finally:
            self._in_flight.pop(msg_id, None)

    async def _read_fsci_serial(self) -> None:
        reply = await self._send_request(
            lambda: self._codec.build_get_attribute(ATTR_SERIAL)
        )
        if reply is None:
            return
        status = parse_response_status(reply)
        if status != STATUS_SUCCESS:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_SERIAL CONFIRM status=%s",
                self.address,
                status_name(status) if status >= 0 else "MalformedFrame",
            )
            return
        values = parse_get_attribute_payload(reply, ATTR_SERIAL)
        if not values:
            return
        try:
            self.state.serial = values[0].decode("utf-8", errors="replace").strip()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "AIPrimeHub %s: serial decode failed: %s", self.address, err
            )

    async def _read_fsci_firmware(self) -> None:
        """Round-trip GET ATTR_FIRMWARE_VERSION(11), populate state.firmware.

        Decode strategy (best-effort, in priority order):
          1. Printable ASCII → use as-is (covers "1.0", "1.2.3-rc4", etc.).
          2. 1-4 bytes → packed version bytes joined with "." (covers
             0x04 0x02 0x01 0x01 → "4.2.1.1").
          3. Anything else → hex string for diagnostic visibility.
        """
        reply = await self._send_request(
            lambda: self._codec.build_get_attribute(ATTR_FIRMWARE_VERSION)
        )
        if reply is None:
            return
        status = parse_response_status(reply)
        if status != STATUS_SUCCESS:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_FIRMWARE_VERSION CONFIRM status=%s",
                self.address,
                status_name(status) if status >= 0 else "MalformedFrame",
            )
            return
        values = parse_get_attribute_payload(reply, ATTR_FIRMWARE_VERSION)
        if not values:
            return
        raw = values[0]
        decoded = self._decode_firmware_value(raw)
        self.state.firmware = decoded
        _LOGGER.debug(
            "AIPrimeHub %s: ATTR_FIRMWARE_VERSION raw=%s decoded=%s",
            self.address, to_hex(raw), decoded,
        )

    @staticmethod
    def _decode_firmware_value(raw: bytes) -> str:
        if not raw:
            return ""
        try:
            text = raw.decode("utf-8", errors="strict").strip()
            if text and all(c.isprintable() and ord(c) < 127 for c in text):
                return text
        except UnicodeDecodeError:
            pass
        if 1 <= len(raw) <= 4:
            return ".".join(str(b) for b in raw)
        return raw.hex()

    async def _read_device_info(self, client: BleakClientWithServiceCache) -> None:
        try:
            info = await read_device_info(client)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "AIPrimeHub %s: 0x180A read failed: %s", self.address, err
            )
            return
        self.state.manufacturer = info.manufacturer
        self.state.model_number = info.model_number
        self.state.serial_number = info.serial_number
        self.state.hardware_revision = info.hardware_revision
        self.state.firmware_revision = info.firmware_revision
        self.state.software_revision = info.software_revision

    async def _async_discover_channels(self) -> None:
        reply = await self._send_request(
            lambda: self._codec.build_get_attribute(
                ATTR_CHANNEL_LIST, instance=0, count=0xFF
            )
        )
        if reply is None:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_CHANNEL_LIST read returned no frame "
                "after retries; keeping initialized channel set", self.address,
            )
            return
        _LOGGER.debug(
            "AIPrimeHub %s: ATTR_CHANNEL_LIST raw reply: %s",
            self.address, to_hex(reply),
        )
        status = parse_response_status(reply)
        if status != STATUS_SUCCESS:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_CHANNEL_LIST CONFIRM status=%s",
                self.address,
                status_name(status) if status >= 0 else "MalformedFrame",
            )
            return
        values = parse_get_attribute_payload(reply, ATTR_CHANNEL_LIST)
        if not values:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_CHANNEL_LIST parsed empty value list; "
                "keeping initialized channel set", self.address,
            )
            return
        _LOGGER.debug(
            "AIPrimeHub %s: ATTR_CHANNEL_LIST parsed %d entries: %s",
            self.address, len(values), [v.hex() for v in values],
        )

        discovered: list[int] = []
        for raw in values:
            if not raw:
                continue
            discovered.append(raw[0])

        if not discovered:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_CHANNEL_LIST extracted no channel IDs; "
                "keeping initialized channel set", self.address,
            )
            return

        new_channels: dict[int, ChannelState] = {}
        for cid in discovered:
            existing = self.state.channels.get(cid)
            new_channels[cid] = ChannelState(
                channel_id=cid,
                label=CHANNEL_DEFAULT_LABELS.get(cid, f"Channel 0x{cid:02X}"),
                value_device=existing.value_device if existing else 0,
                is_fan=(cid == CHANNEL_ID_FAN),
            )
        self.state.channels = new_channels

        unknown = [cid for cid in discovered if cid not in CHANNEL_DEFAULT_LABELS]
        if unknown:
            _LOGGER.info(
                "AIPrimeHub %s: discovered %d channels including %d "
                "without default labels: %s",
                self.address, len(discovered), len(unknown),
                ", ".join(f"0x{cid:02X}" for cid in unknown),
            )

    async def _async_read_channel_state(self) -> None:
        """Per-channel GET ATTR_LIVE_CHANNEL_STATE; updates state.channels values.

        Each per-channel request now goes through `_send_request`'s retry
        wrapper (3 attempts, 5s/3s/3s timeouts, 0.5s/1.0s backoffs). With
        ~30% per-attempt success against the BT-proxy-induced fragmentation
        loss, expect ~63% per-call success and ~94% across 3 poll cycles.

        Channels 0x01 (fan) and 0x1E (likely Moonlight) return InvalidElement
        on attribute 1504 — both are system-managed, not user-targetable.
        Their state simply doesn't update from this poll; that's intentional.

        PR-3c: when a nonzero value is read, opportunistically populate
        _last_nonzero_values so a subsequent HA-initiated "Master ON" can
        restore the channel to its actual prior brightness (including values
        set by Mobius / device schedules, not just HA writes).
        """
        if not self.state.channels:
            _LOGGER.debug(
                "AIPrimeHub %s: no channels known; skipping state read",
                self.address,
            )
            return

        any_updated = False
        for channel_id in list(self.state.channels):
            reply = await self._send_request(
                lambda cid=channel_id: self._codec.build_get_attribute(
                    ATTR_LIVE_CHANNEL_STATE, instance=cid, count=1
                )
            )
            if reply is None:
                continue
            status = parse_response_status(reply)
            if status != STATUS_SUCCESS:
                _LOGGER.debug(
                    "AIPrimeHub %s: channel 0x%02X state CONFIRM status=%s",
                    self.address, channel_id,
                    status_name(status) if status >= 0 else "MalformedFrame",
                )
                continue
            values = parse_get_attribute_payload(reply, ATTR_LIVE_CHANNEL_STATE)
            if not values:
                continue
            raw = values[0]
            if len(raw) < CHANNEL_STATE_ITEM_LEN:
                _LOGGER.debug(
                    "AIPrimeHub %s: channel 0x%02X value too short (got %d, need %d): %s",
                    self.address, channel_id, len(raw),
                    CHANNEL_STATE_ITEM_LEN, to_hex(raw),
                )
                continue
            value = int.from_bytes(raw[:CHANNEL_STATE_ITEM_LEN], "little")
            value = max(0, min(DEVICE_VALUE_MAX, value))
            if value > 0:
                self._last_nonzero_values[channel_id] = value
            cs = self.state.channels.get(channel_id)
            if cs is not None and cs.value_device != value:
                cs.value_device = value
                any_updated = True

        if any_updated:
            self._notify_state_changed()

    async def _async_poll_state_callback(self, _now: datetime | None = None) -> None:
        if not self.state.ble_connected:
            return
        if self._poll_lock.locked():
            return
        async with self._poll_lock:
            try:
                await self._async_read_channel_state()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "AIPrimeHub %s: periodic poll errored: %s",
                    self.address, err,
                )

    @callback
    def _handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        if self.state.rssi != service_info.rssi:
            self.state.rssi = service_info.rssi
            self._notify_state_changed()

    async def async_set_channel(self, channel_id: int, value_device: int) -> None:
        """Write one channel's target brightness via FSCI SET.

        PR-3c (2026-06-06): replaces the v0.0.1 stub. Strategy:
          1. Validate (channel known, channel writable) — early returns for
             skip cases.
          2. Optimistic local-state update + notify so HA UI reacts instantly.
          3. Send the FSCI SET via `_send_request` (retry wrapper).
          4. On any failure (retry-exhausted / status != Success), revert the
             local-state and notify again, leaving the slider showing reality.
          5. On success, opportunistically remember the value for Master-ON
             restore.

        Caveat: rapid back-to-back writes on the same channel can race —
        if write A and write B are both in flight, A's failure-revert can
        revert B's optimistic state. Not a problem for typical slider use
        (HA serializes per-entity service calls); revisit if it bites in
        practice. See task notes for the deterministic fix (re-poll instead
        of revert).
        """
        if channel_id not in self.state.channels:
            _LOGGER.warning(
                "AIPrimeHub %s: refusing to set unknown channel 0x%02X",
                self.address, channel_id,
            )
            return
        if channel_id in _UNWRITABLE_CHANNEL_IDS:
            _LOGGER.debug(
                "AIPrimeHub %s: skipping write to unwritable channel 0x%02X "
                "(returns InvalidElement by design — fan / schedule-only)",
                self.address, channel_id,
            )
            return

        clamped = max(0, min(DEVICE_VALUE_MAX, int(value_device)))
        prev_value = self.state.channels[channel_id].value_device

        # Optimistic update — HA UI sees the new value immediately.
        self.state.channels[channel_id].value_device = clamped
        self._notify_state_changed()

        reply = await self._send_request(
            lambda: self._codec.build_set_channel(channel_id, clamped)
        )
        if reply is None:
            _LOGGER.warning(
                "AIPrimeHub %s: set channel 0x%02X to %d exhausted retries; "
                "reverting local state to %d",
                self.address, channel_id, clamped, prev_value,
            )
            self.state.channels[channel_id].value_device = prev_value
            self._notify_state_changed()
            return

        status = parse_response_status(reply)
        if status != STATUS_SUCCESS:
            _LOGGER.warning(
                "AIPrimeHub %s: set channel 0x%02X to %d CONFIRM status=%s; "
                "reverting local state to %d",
                self.address, channel_id, clamped,
                status_name(status) if status >= 0 else "MalformedFrame",
                prev_value,
            )
            self.state.channels[channel_id].value_device = prev_value
            self._notify_state_changed()
            return

        # Success — remember the value for Master-ON restore.
        if clamped > 0:
            self._last_nonzero_values[channel_id] = clamped
        _LOGGER.debug(
            "AIPrimeHub %s: set channel 0x%02X to %d OK",
            self.address, channel_id, clamped,
        )

    async def async_set_power(self, *, on: bool) -> None:
        """Turn the fixture on/off by writing each writable LED channel.

        PR-3c (2026-06-06): replaces the v0.0.1 stub. Writes are issued
        sequentially via `async_set_channel` (which handles retries and
        rollback per-channel). Fan and known-unwritable channels are
        skipped by `async_set_channel` itself.

        "On" semantics:
          - Use the last-known nonzero value for each channel (captured
            either from a prior HA write or from a periodic state poll
            that observed Mobius / schedule-set brightness).
          - Fall back to 50% of full scale if we've never seen the channel
            nonzero (e.g., HA boot before the first successful poll).

        "Off" semantics:
          - Write 0 to every writable LED channel.
        """
        for channel_id in list(self.state.channels):
            if channel_id in _UNWRITABLE_CHANNEL_IDS:
                continue
            if on:
                target = self._last_nonzero_values.get(
                    channel_id, _POWER_ON_FALLBACK_DEVICE_VALUE
                )
            else:
                target = 0
            await self.async_set_channel(channel_id, target)

        _LOGGER.debug(
            "AIPrimeHub %s: set power=%s complete", self.address, on
        )

    def is_on(self) -> bool:
        return any(
            cs.value_device > 0
            for cid, cs in self.state.channels.items()
            if cid != CHANNEL_ID_FAN
        )

    def aggregate_brightness_device(self) -> int:
        led_values = [
            cs.value_device
            for cid, cs in self.state.channels.items()
            if cid != CHANNEL_ID_FAN
        ]
        return max(led_values) if led_values else 0

    def _initialize_channels(self) -> None:
        for cid in ALL_CHANNEL_IDS:
            self.state.channels[cid] = ChannelState(
                channel_id=cid,
                label=CHANNEL_DEFAULT_LABELS.get(cid, f"Channel 0x{cid:02X}"),
                value_device=0,
                is_fan=(cid == CHANNEL_ID_FAN),
            )

    def _notify_state_changed(self) -> None:
        async_dispatcher_send(
            self.hass,
            SIGNAL_STATE_UPDATED.format(entry=self.entry.entry_id),
        )

    def _notify_availability_changed(self) -> None:
        async_dispatcher_send(
            self.hass,
            SIGNAL_AVAILABILITY.format(entry=self.entry.entry_id),
            self.state.ble_connected,
        )

    @property
    def signal_state_updated(self) -> str:
        return SIGNAL_STATE_UPDATED.format(entry=self.entry.entry_id)

    @property
    def signal_availability(self) -> str:
        return SIGNAL_AVAILABILITY.format(entry=self.entry.entry_id)


def get_hub(hass: HomeAssistant, entry: ConfigEntry) -> AIPrimeHub:
    domain_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    return domain_data["hub"]
