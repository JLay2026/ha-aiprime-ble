"""Central hub: owns the BLE connection, holds device state, routes writes.

PR-2 (2026-06-02) — replaces the v0.0.1 stubs with a real BLE connection
lifecycle (connect/disconnect, RX dispatch, post-connect ATTR_SERIAL + 0x180A
reads, passive RSSI tracking, reconnect loop with exponential backoff).

PR-3a (2026-06-02) — adds READ-ONLY periodic state poll:
  - Channel-list DISCOVERY at connect via GET ATTR_CHANNEL_LIST (901).
  - Per-channel GET ATTR_LIVE_CHANNEL_STATE(channel_id) every 30s.

Hot-fix (2026-06-02) — `_async_read_channel_state` now polls the correct
attribute (1504) with the right encoding. PR-3a was polling attribute 1500
which empirically returns a 2-byte status word always equal to 0 — so the
dashboard tiles all read 0% even when the schedule was driving the LEDs.
The probe script (aiprime_channel_probe.py) confirmed 1504 returns 4-byte
uint32 LE in 0..20000. See const.py for the recorded findings.

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
    ATTR_LIVE_CHANNEL_STATE,
    ATTR_SERIAL,
    CHANNEL_DEFAULT_LABELS,
    CHANNEL_ID_FAN,
    CHANNEL_STATE_ITEM_LEN,
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

# Per-request timeout for an FSCI round-trip (Day 3 saw ~tens of ms; this is
# generous to cover BLE retry behavior on the ESP32 proxy hop).
_REQUEST_TIMEOUT_S = 5.0


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

        self._poll_lock = asyncio.Lock()

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
                _LOGGER.warning(
                    "AIPrimeHub %s: BLE device not found in HA bluetooth "
                    "cache; will retry", self.address,
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
                _LOGGER.warning(
                    "AIPrimeHub %s: connect failed: %s", self.address, err
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

            self.state.ble_connected = True
            self._reconnect_backoff = DEFAULT_RECONNECT_BACKOFF_INITIAL_S

            await self._read_fsci_serial()
            await self._async_discover_channels()
            await self._async_read_channel_state()
            await self._read_device_info(client)

            self._notify_availability_changed()
            self._notify_state_changed()
            _LOGGER.info(
                "AIPrimeHub %s: connected; serial=%s manufacturer=%s "
                "model=%s firmware=%s channels=%s",
                self.address,
                self.state.serial,
                self.state.manufacturer,
                self.state.model_number,
                self.state.firmware_revision,
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
                "AIPrimeHub %s: unmatched RX msg_id=%d: %s",
                self.address, msg_id, to_hex(frame),
            )
            return
        future.set_result(frame)

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
                self.address, msg_id, timeout,
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "AIPrimeHub %s: FSCI request msg_id=%d failed: %s",
                self.address, msg_id, err,
            )
            return None
        finally:
            self._in_flight.pop(msg_id, None)

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
        msg_id, frame = self._codec.build_get_attribute(
            ATTR_CHANNEL_LIST, instance=0, count=0xFF
        )
        reply = await self._send_request(msg_id, frame)
        if reply is None:
            _LOGGER.warning(
                "AIPrimeHub %s: ATTR_CHANNEL_LIST read returned no frame; "
                "keeping initialized channel set", self.address,
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

        Hot-fix 2026-06-02: reads CHANNEL_STATE_ITEM_LEN (=4) bytes as uint32 LE
        and clamps to DEVICE_VALUE_MAX (=20000). PR-3a originally read 2 bytes
        and clamped to 1000 per the per-mille assumption inherited from the
        pump project — wrong for this product. See const.py "Channel state
        attributes" block + CHANGELOG hot-fix entry for the probe data that
        established the right encoding.

        Channels 0x01 (fan) and 0x1E (likely Moonlight) return InvalidElement
        on the ATTR_LIVE_CHANNEL_STATE (1504) attribute — both are system-
        managed, not user-targetable. Their state simply doesn't update from
        this poll; that's intentional, not a bug.
        """
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
                # Expected for channels 0x01 (fan) and 0x1E (likely Moonlight);
                # logged at DEBUG so it's visible during troubleshooting
                # without spamming WARN-level for benign cases.
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
        if channel_id not in self.state.channels:
            _LOGGER.warning(
                "AIPrimeHub %s: refusing to set unknown channel 0x%02X",
                self.address, channel_id,
            )
            return
        clamped = max(0, min(DEVICE_VALUE_MAX, int(value_device)))
        self.state.channels[channel_id].value_device = clamped
        _LOGGER.debug(
            "AIPrimeHub %s: STUB set channel 0x%02X -> %d (PR-3c will FSCI-write)",
            self.address, channel_id, clamped,
        )
        self._notify_state_changed()

    async def async_set_power(self, *, on: bool) -> None:
        if on:
            # 50% as placeholder — PR-3c will remember last-state.
            placeholder = DEVICE_VALUE_MAX // 2
            for cid in self.state.channels:
                if cid != CHANNEL_ID_FAN:
                    self.state.channels[cid].value_device = placeholder
        else:
            for cid in self.state.channels:
                if cid != CHANNEL_ID_FAN:
                    self.state.channels[cid].value_device = 0
        _LOGGER.debug(
            "AIPrimeHub %s: STUB set power=%s (PR-3c will FSCI-write)",
            self.address, on,
        )
        self._notify_state_changed()

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
    """Convenience accessor used by platform setup functions."""
    domain_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    return domain_data["hub"]
