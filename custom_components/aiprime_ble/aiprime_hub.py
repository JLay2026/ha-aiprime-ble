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

Hot-fix (2026-06-02) — channel-state poll reads CHANNEL_STATE_ITEM_LEN (=4)
bytes as uint32 LE at attribute 1504.

Retry-with-backoff — `_send_request` is a retry wrapper around a single
attempt `_send_request_once` (3 attempts, 5s/3s/3s, 0.5s/1.0s backoffs).

PR-3c..3f (2026-06-06/07) — per-channel write attempts to attrs 1504/1513,
and a CHAR_AUX experiment. All returned SUCCESS (or AUX timeouts) but never
drove the light. Root cause via iOS PacketLogger capture of myAI
(2026-06-10): 1504/1513 are read-only live-state views; the real control
write is attribute 407 as an ALL-CHANNEL bulk SET.
[[aiprime-write-protocol-decoded]]

PR-4 (2026-06-10) — implement the decoded control path: writes go to
CHAR_TX_DATA; self._desired holds per-channel write-scale (0..1000) values;
every control action sends ONE codec.build_set_all_channels frame (attr 407)
for all 7 channels. async_set_channel / async_set_power funnel through
_write_all_channels(ramp) with optimistic UI + revert on failure.

PR-4c (2026-06-11) — write priming. PR-4 writes reached the device
(SUCCESS) but it didn't apply them while a schedule was active, whereas
myAI's byte-identical writes DID. The full myAI session decode shows the
only difference: myAI reads a batch of config/state/schedule attributes
(201/207/205/206, 907/905/903/904/902, 500/511) at connect before writing.
We now replicate that:
  - _async_prime() issues those batched GET reads (best-effort).
  - Called once at connect, and again (schedule re-read) before each write.

Reconnect topology: one long-running `_connect_with_retry` task per
"connection epoch"; the bleak disconnect callback spawns a fresh task when
the link drops, guarded by `_connect_task` and `_connect_lock`.
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
    CHANNEL_WRITE_ORDER,
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
    DEVICE_WRITE_VALUE_MAX,
    DOMAIN,
    MYAI_PRIME_READ_GROUPS,
    MYAI_WRITE_PRIME_GROUP,
    RAMP_POWER,
    RAMP_SLIDER,
    SIGNAL_AVAILABILITY,
    SIGNAL_STATE_UPDATED,
    device_read_to_write,
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

# PR-4 (2026-06-10): writes go to attribute 407 as an all-channel bulk SET
# (decoded from myAI). PR-4c (2026-06-11): a connect-time + per-write read
# preamble (see const.MYAI_PRIME_READ_GROUPS) primes the device so it honors
# the writes. Write characteristic is CHAR_TX_DATA, confirmed by the capture
# (writes land on GATT handle 0x002b).
_WRITE_CHARACTERISTIC = CHAR_TX_DATA

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
        # restore. Populated by writes AND by periodic polls.
        self._last_nonzero_values: dict[int, int] = {}

        # PR-4: desired write-state per channel in WRITE scale (0..1000).
        # myAI controls the light by writing ALL channels at once to attr 407;
        # we mirror that. Seeded to 0, updated from reads + HA writes.
        self._desired: dict[int, int] = {cid: 0 for cid in ALL_CHANNEL_IDS}

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

            # PR-3e: also subscribe to CHAR_AUX notify (harmless; kept across
            # PR-4/4c in case a future probe needs it). Best-effort.
            try:
                await client.start_notify(CHAR_AUX, self._on_rx_final)
                _LOGGER.debug(
                    "AIPrimeHub %s: CHAR_AUX notify subscribed",
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
            # PR-4c: myAI's connect-time read preamble — the device requires
            # priming before it honors live attr-407 control writes.
            await self._async_prime(MYAI_PRIME_READ_GROUPS, "connect")
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

        Each attempt invokes `builder()` fresh so a late response to a
        timed-out attempt is discarded (logged as unmatched RX) rather than
        shadowing the new request. Stops retrying on BLE disconnect.
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
        """Single-attempt FSCI round-trip. Writes go to _WRITE_CHARACTERISTIC
        (CHAR_TX_DATA, confirmed by the myAI iOS capture)."""
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
        """Round-trip GET ATTR_FIRMWARE_VERSION(11), populate state.firmware."""
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

        Channels 0x01 (fan) and 0x1E return InvalidElement on attribute 1504
        — system-managed, don't read back; intentional. Nonzero reads populate
        _last_nonzero_values (Master-ON restore) and seed the write-scale
        _desired mirror (PR-4).
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
            # PR-4: keep the write-scale desired mirror in sync with reality
            # for the channels we can actually read (0x01 / 0x1E never read
            # back — they stay at their last HA-written value).
            self._desired[channel_id] = device_read_to_write(value)
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

    async def _async_prime(self, groups, label: str) -> None:
        """Issue myAI's batched "priming" GET reads (best-effort).

        Decoded from the myAI capture: the device only honors live attr-407
        control writes after being primed by reading config/schedule state
        (see const.MYAI_PRIME_READ_GROUPS). Responses are intentionally
        ignored — we just need the device to process the reads. Failures are
        swallowed so a flaky prime never blocks the subsequent write.
        """
        for group in groups:
            try:
                await self._send_request(
                    lambda g=group: self._codec.build_get_multi(g),
                    retries=0,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "AIPrimeHub %s: prime read (%s) errored (ignored): %s",
                    self.address, label, err,
                )
        _LOGGER.debug("AIPrimeHub %s: prime reads (%s) done", self.address, label)

    async def _write_all_channels(self, ramp: int) -> bool:
        """Write the full desired channel set via attribute 407 (PR-4).

        One FSCI SET to attr 407 carries all 7 channels at once. Returns True
        on a Success CONFIRM, False otherwise (caller may revert optimistic UI).
        """
        # PR-4c: re-prime (read schedule) immediately before the control
        # write, mirroring the device-state myAI establishes before writing.
        await self._async_prime((MYAI_WRITE_PRIME_GROUP,), "pre-write")

        reply = await self._send_request(
            lambda: self._codec.build_set_all_channels(dict(self._desired), ramp)
        )
        if reply is None:
            _LOGGER.warning(
                "AIPrimeHub %s: attr-407 channel write exhausted retries",
                self.address,
            )
            return False
        status = parse_response_status(reply)
        if status != STATUS_SUCCESS:
            _LOGGER.warning(
                "AIPrimeHub %s: attr-407 channel write CONFIRM status=%s",
                self.address,
                status_name(status) if status >= 0 else "MalformedFrame",
            )
            return False
        _LOGGER.debug(
            "AIPrimeHub %s: attr-407 channel write OK (ramp=0x%02x) desired=%s",
            self.address, ramp,
            {f"0x{c:02x}": v for c, v in self._desired.items()},
        )
        return True

    async def async_set_channel(self, channel_id: int, value_device: int) -> None:
        """Set one channel; writes the FULL channel set via attr 407 (PR-4).

        value_device is in the READ scale (0..DEVICE_VALUE_MAX) from
        number.py's percent_to_device(); converted to write scale (0..1000).

        CAVEAT: channels 0x01 (fan) and 0x1E never read back, so their desired
        value is whatever HA last wrote (default 0). Follow-up PR-4b: seed
        full state by reading attr 407 at connect.
        """
        if channel_id not in self.state.channels:
            _LOGGER.warning(
                "AIPrimeHub %s: refusing to set unknown channel 0x%02X",
                self.address, channel_id,
            )
            return

        clamped_read = max(0, min(DEVICE_VALUE_MAX, int(value_device)))
        prev_read = self.state.channels[channel_id].value_device
        prev_desired = self._desired.get(channel_id, 0)

        # Optimistic local update (read-scale for the entity, write-scale mirror)
        self.state.channels[channel_id].value_device = clamped_read
        self._desired[channel_id] = device_read_to_write(clamped_read)
        self._notify_state_changed()

        ok = await self._write_all_channels(RAMP_SLIDER)
        if not ok:
            # revert both mirrors
            self.state.channels[channel_id].value_device = prev_read
            self._desired[channel_id] = prev_desired
            self._notify_state_changed()
            return

        if clamped_read > 0:
            self._last_nonzero_values[channel_id] = clamped_read

    async def async_set_power(self, *, on: bool) -> None:
        """Turn the fixture on/off via a single attr-407 bulk write (PR-4).

        Off: every LED channel desired to 0. On: restore each LED channel's
        last-known nonzero value, falling back to full scale if never seen.
        Fan (0x01) left as-is. One bulk write applies all of it.
        """
        prev_desired = dict(self._desired)
        prev_reads = {c: cs.value_device for c, cs in self.state.channels.items()}

        for channel_id in self.state.channels:
            if channel_id == CHANNEL_ID_FAN:
                continue
            if on:
                read_val = self._last_nonzero_values.get(channel_id, DEVICE_VALUE_MAX)
            else:
                read_val = 0
            self.state.channels[channel_id].value_device = read_val
            self._desired[channel_id] = device_read_to_write(read_val)
        self._notify_state_changed()

        ok = await self._write_all_channels(RAMP_POWER)
        if not ok:
            self._desired = prev_desired
            for c, v in prev_reads.items():
                if c in self.state.channels:
                    self.state.channels[c].value_device = v
            self._notify_state_changed()
            return

        _LOGGER.debug("AIPrimeHub %s: set power=%s complete", self.address, on)

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
