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
  - First 5 connect attempts log at DEBUG instead of WARNING. HA boot
    typically takes ~5-25s for the bluetooth integration to populate
    its device cache, and the noisy "BLE device not found in cache;
    will retry" stream during that window isn't actionable. Real
    failures (after attempt 6 ≈ ~60s of sustained backoff) still
    escalate to WARNING.

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
from typing import Any

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
    CHAR_RX_DATA,
    CHAR_RX_FINAL,
    CHAR_TX_DATA,
    CONF_ADDRESS,
    CONF_NAME,
    DEFAULT_RECONNECT_BACKOFF_CAP_S,
    DEFAULT_RECONNECT_BACKOFF_INITIAL_S,
    DEFAULT_STATE_POLL_INTERVAL_S,
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

# Per-request timeout for an FSCI round-trip (Day 3 saw ~tens of ms; this is
# generous to cover BLE retry behavior on the ESP32 proxy hop).
_REQUEST_TIMEOUT_S = 5.0

# Attempt threshold below which connect failures log at DEBUG instead of
# WARNING. PR-3b polish. HA boot typically takes ~5-25s for the bluetooth
# integration to populate its device cache; warnings during that window
# aren't actionable.
_QUIET_CONNECT_ATTEMPTS = 5


class AIPrimeHub:
    """Owns the BLE session and the in-memory device state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data.get(CONF_ADDRESS, "")
        self.name: str = entry.data.get(CONF_NAME, "AI Prime")
        self.state = DeviceState(address=self.address, name=self.name)
        self._initialize_channels()

        # FSCI protocol machinery
        self._codec = FsciCodec()
        self._client: BleakClientWithServiceCache | None = None
        self._rx_buffer = bytearray()
        self._in_flight: dict[int, asyncio.Future[bytes]] = {}

        # Connection lifecycle
        self._connect_lock = asyncio.Lock()
        self._connect_task: asyncio.Task[None] | None = None
        self._reconnect_backoff: float = DEFAULT_RECONNECT_BACKOFF_INITIAL_S
        self._intentional_disconnect: bool = False
        # PR-3b: attempt counter consumed by _attempt_log_level().
        self._connect_attempt: int = 0

        # State poll (PR-3a)
        self._poll_lock = asyncio.Lock()

    # --- HA lifecycle -----------------------------------------------------

    async def async_setup(self) -> None:
        """Register passive RSSI tracking + periodic state poll, then connect."""
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

    # --- Connection management -------------------------------------------

    def _spawn_connect_task(self) -> None:
        if self._intentional_disconnect:
            return
        if self._connect_task and not self._connect_task.done():
            return
        self._connect_task = self.hass.async_create_task(
            self._connect_with_retry()
        )

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
                    self.address,
                    attempt,
                    err,
                )

            if self.state.ble_connected:
                return

            if self._intentional_disconnect:
                return

            delay = self._reconnect_backoff
            self._reconnect_backoff = min(
                delay * 2, DEFAULT_RECONNECT_BACKOFF_CAP_S
            )
            _LOGGER.debug(
                "AIPrimeHub %s: reconnect attempt %d in %.1fs",
                self.address,
                attempt + 1,
                delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    def _attempt_log_level(self) -> int:
        """DEBUG for first `_QUIET_CONNECT_ATTEMPTS` attempts; WARNING after.

        Used by `_async_connect` to suppress the boot-noise stream of
        "BLE device not found in cache" and similar transient failures
        while HA's bluetooth integration is warming up.
        """
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
                    self.address,
                    self._connect_attempt,
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
                    self.address,
                    self._connect_attempt,
                    err,
                )
                return

            self._client = client
            self._rx_buffer.clear()

            try:
                await client.start_notify(CHAR_RX_DATA, self._on_rx_data)
                await client.start_notify(CHAR_RX_FINAL, self._on_rx_final)
            except Exception as err:  # noqa: BLE001
                # RX subscribe failure means service tree drift or
                # permission issue — always warn, not transient.
                _LOGGER.warning(
                    "AIPrimeHub %s: RX subscribe failed: %s", self.address, err
                )
                await self._async_disconnect()
                return

            self.state.ble_connected = True
            self._reconnect_backoff = DEFAULT_RECONNECT_BACKOFF_INITIAL_S

            # FSCI smoke test: GET ATTR_SERIAL(3). Same query as Day 3.
            await self._read_fsci_serial()

            # PR-3b: FSCI firmware version (lights up the Firmware sensor).
            await self._read_fsci_firmware()

            # PR-3a: discover the real channel set from the device.
            await self._async_discover_channels()

            # PR-3a: initial channel state snapshot.
            await self._async_read_channel_state()

            # Standard 0x180A device info — best-effort, never aborts setup.
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
                    self.address,
                    err,
                )
        if was_connected:
            self._notify_availability_changed()

    def _handle_disconnected(self, _client: Any) -> None:
        _LOGGER.debug(
            "AIPrimeHub %s: bleak disconnected callback", self.address
        )
        self.state.ble_connected = False
        for future in list(self._in_flight.values()):
            if not future.done():
                future.cancel()
        self._in_flight.clear()
        self._notify_availability_changed()
        if not self._intentional_disconnect:
            # Fresh epoch — reset attempt counter + backoff so a new
            # connect-with-retry loop logs its early attempts at DEBUG.
            self._connect_attempt = 0
            self._reconnect_backoff = DEFAULT_RECONNECT_BACKOFF_INITIAL_S
            self._spawn_connect_task()

    # --- RX path (BLE notifications) -------------------------------------

    def _on_rx_data(self, _ch: Any, data: bytearray) -> None:
        self._rx_buffer.extend(data)

    def _on_rx_final(self, _ch: Any, data: bytearray) -> None:
        self._rx_buffer.extend(data)
        frame = bytes(self._rx_buffer)
        self._rx_buffer.clear()
        if len(frame) < 5:
            _LOGGER.debug(
                "AIPrimeHub %s: RX frame too short to dispatch: %s",
                self.address,
                to_hex(frame),
            )
            return
        msg_id = frame[3] | (frame[4] << 8)
        future = self._in_flight.get(msg_id)
        if future is None or future.done():
            _LOGGER.debug(
                "AIPrimeHub %s: unmatched RX msg_id=%d: %s",
                self.address,
                msg_id,
                to_hex(frame),
            )
            return
        future.set_result(frame)

    # --- TX path (FSCI requests) -----------------------------------------

    async def _send_request(
        self, msg_id: int, frame: bytes, timeout: float = _REQUEST_TIMEOUT_S
    ) -> bytes | None:
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
            await client.write_gatt_char(CHAR_TX_DATA, frame, response=False)
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "AIPrimeHub %s: FSCI request msg_id=%d timed out after %.1fs",
                self.address,
                msg_id,
                timeout,
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "AIPrimeHub %s: FSCI request msg_id=%d failed: %s",
                self.address,
                msg_id,
                err,
            )
            return None
        finally:
            self._in_flight.pop(msg_id, None)

    # --- Post-connect reads ----------------------------------------------

    async def _read_fsci_serial(self) -> None:
        msg_id, frame = self._codec.build_get_attribute(ATTR_SERIAL)
        reply = await self._send_request(msg_id, frame)
        if reply is None:
            _LOGGER.debug(
                "AIPrimeHub %s: ATTR_SERIAL read returned no frame", self.address
            )
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
            _LOGGER.debug(
                "AIPrimeHub %s: ATTR_SERIAL parsed empty value list",
                self.address,
            )
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

        Raw payload is logged at DEBUG either way so the actual wire bytes
        are visible if decode picks the wrong strategy.
        """
        msg_id, frame = self._codec.build_get_attribute(ATTR_FIRMWARE_VERSION)
        reply = await self._send_request(msg_id, frame)
        if reply is None:
            _LOGGER.debug(
                "AIPrimeHub %s: ATTR_FIRMWARE_VERSION read returned no frame",
                self.address,
            )
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
            _LOGGER.debug(
                "AIPrimeHub %s: ATTR_FIRMWARE_VERSION parsed empty value list",
                self.address,
            )
            return
        raw = values[0]
        decoded = self._decode_firmware_value(raw)
        self.state.firmware = decoded
        _LOGGER.debug(
            "AIPrimeHub %s: ATTR_FIRMWARE_VERSION raw=%s decoded=%s",
            self.address,
            to_hex(raw),
            decoded,
        )

    @staticmethod
    def _decode_firmware_value(raw: bytes) -> str:
        """Pick the most-readable interpretation of an FSCI firmware payload."""
        if not raw:
            return ""
        # Strategy 1: printable ASCII.
        try:
            text = raw.decode("utf-8", errors="strict").strip()
            if text and all(c.isprintable() and ord(c) < 127 for c in text):
                return text
        except UnicodeDecodeError:
            pass
        # Strategy 2: packed version bytes joined with ".".
        if 1 <= len(raw) <= 4:
            return ".".join(str(b) for b in raw)
        # Strategy 3: hex fallback.
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

    # --- PR-3a: channel discovery + state poll ---------------------------

    async def _async_discover_channels(self) -> None:
        msg_id, frame = self._codec.build_get_attribute(
            ATTR_CHANNEL_LIST, instance=0, count=0xFF
        )
        reply = await self._send_request(msg_id, frame)
        if reply is None:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_CHANNEL_LIST read returned no frame; "
                "keeping initialized channel set",
                self.address,
            )
            return
        _LOGGER.debug(
            "AIPrimeHub %s: ATTR_CHANNEL_LIST raw reply: %s",
            self.address,
            to_hex(reply),
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
                "keeping initialized channel set",
                self.address,
            )
            return
        _LOGGER.debug(
            "AIPrimeHub %s: ATTR_CHANNEL_LIST parsed %d entries: %s",
            self.address,
            len(values),
            [v.hex() for v in values],
        )

        discovered: list[int] = []
        for raw in values:
            if not raw:
                continue
            discovered.append(raw[0])

        if not discovered:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_CHANNEL_LIST extracted no channel IDs; "
                "keeping initialized channel set",
                self.address,
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
                self.address,
                len(discovered),
                len(unknown),
                ", ".join(f"0x{cid:02X}" for cid in unknown),
            )

    async def _async_read_channel_state(self) -> None:
        if not self.state.channels:
            _LOGGER.debug(
                "AIPrimeHub %s: no channels known; skipping state read",
                self.address,
            )
            return

        any_updated = False
        for channel_id in list(self.state.channels):
            msg_id, frame = self._codec.build_get_attribute(
                ATTR_LIVE_CHANNEL_STATE, instance=channel_id, count=1
            )
            reply = await self._send_request(msg_id, frame)
            if reply is None:
                continue
            status = parse_response_status(reply)
            if status != STATUS_SUCCESS:
                _LOGGER.debug(
                    "AIPrimeHub %s: channel 0x%02X state CONFIRM status=%s",
                    self.address,
                    channel_id,
                    status_name(status) if status >= 0 else "MalformedFrame",
                )
                continue
            values = parse_get_attribute_payload(reply, ATTR_LIVE_CHANNEL_STATE)
            if not values:
                continue
            raw = values[0]
            if len(raw) < 2:
                _LOGGER.debug(
                    "AIPrimeHub %s: channel 0x%02X value too short: %s",
                    self.address,
                    channel_id,
                    to_hex(raw),
                )
                continue
            value = int.from_bytes(raw[:2], "little")
            value = max(0, min(1000, value))
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
                    self.address,
                    err,
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

    # --- Public control surface (stubs — PR-3c implements writes) --------

    async def async_set_channel(self, channel_id: int, value_device: int) -> None:
        if channel_id not in self.state.channels:
            _LOGGER.warning(
                "AIPrimeHub %s: refusing to set unknown channel 0x%02X",
                self.address,
                channel_id,
            )
            return
        clamped = max(0, min(1000, int(value_device)))
        self.state.channels[channel_id].value_device = clamped
        _LOGGER.debug(
            "AIPrimeHub %s: STUB set channel 0x%02X -> %d (PR-3c will FSCI-write)",
            self.address,
            channel_id,
            clamped,
        )
        self._notify_state_changed()

    async def async_set_power(self, *, on: bool) -> None:
        if on:
            placeholder = 500
            for cid in self.state.channels:
                if cid != CHANNEL_ID_FAN:
                    self.state.channels[cid].value_device = placeholder
        else:
            for cid in self.state.channels:
                if cid != CHANNEL_ID_FAN:
                    self.state.channels[cid].value_device = 0
        _LOGGER.debug(
            "AIPrimeHub %s: STUB set power=%s (PR-3c will FSCI-write)",
            self.address,
            on,
        )
        self._notify_state_changed()

    # --- Read helpers used by entities -----------------------------------

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

    # --- Internal helpers ------------------------------------------------

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
