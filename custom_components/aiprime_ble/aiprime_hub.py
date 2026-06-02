"""Central hub: owns the BLE connection, holds device state, routes writes.

PR-2 (2026-06-02) — replaces the v0.0.1 stubs with a real BLE connection
lifecycle:
  - HA bluetooth integration resolves BLEDevice from the configured MAC.
  - bleak-retry-connector establishes + keeps the GATT connection.
  - RX_DATA + RX_FINAL subscriptions feed a buffer; on RX_FINAL we extract
    the msg_id and resolve the matching in-flight request future.
  - One FSCI GET ATTR_SERIAL(3) + 0x180A Device Info read happen post-
    connect to populate DeviceState and prove the dispatch wiring works.
  - Passive RSSI tracking via bluetooth.async_register_callback (works even
    when the GATT link is down).
  - Disconnect cancels in-flight futures; reconnect uses exponential backoff
    capped at DEFAULT_RECONNECT_BACKOFF_CAP_S.

Reconnect topology: one long-running `_connect_with_retry` task per
"connection epoch". The task loops with exponential backoff until a connect
succeeds, then exits. The bleak disconnect callback spawns a fresh task
when the link drops — there is at most one connect task in flight at any
time, guarded by `_connect_task` and the per-attempt `_connect_lock`.

PR-3 will replace the still-stubbed async_set_channel / async_set_power
with real FSCI writes. PR-2 deliberately ships no mutating writes — the
risk of silently driving the light to an unexpected state is too high
without the periodic state poll that PR-3 introduces.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    close_stale_connections_by_address,
    establish_connection,
)

from .const import (
    ALL_CHANNEL_IDS,
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

    # --- HA lifecycle -----------------------------------------------------

    async def async_setup(self) -> None:
        """Register passive RSSI tracking + initiate first BLE connection."""
        if not self.address:
            _LOGGER.error("AIPrimeHub setup: no CONF_ADDRESS in entry data")
            return

        # Passive RSSI tracking — works even when GATT is down.
        unsub = bluetooth.async_register_callback(
            self.hass,
            self._handle_advertisement,
            BluetoothCallbackMatcher(address=self.address, connectable=False),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        self.entry.async_on_unload(unsub)

        # Kick off the first connect attempt in the background — don't block
        # entry setup on BLE since the device may be temporarily out of range.
        self._spawn_connect_task()

    async def async_unload(self) -> None:
        """Tear down: cancel reconnect loop, disconnect cleanly."""
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
        """Spawn a fresh `_connect_with_retry` loop if none is already running.

        Called from `async_setup` (initial connect) and from
        `_handle_disconnected` (reconnect after a drop). Safe to call
        concurrently — `_connect_lock` inside `_async_connect` prevents
        actual concurrent connect attempts.
        """
        if self._intentional_disconnect:
            return
        if self._connect_task and not self._connect_task.done():
            return
        self._connect_task = self.hass.async_create_task(
            self._connect_with_retry()
        )

    async def _connect_with_retry(self) -> None:
        """Loop until connected (or until unload marks intentional disconnect)."""
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
                    self.address,
                    attempt,
                    err,
                )

            if self.state.ble_connected:
                return  # Success — loop exits; disconnect callback will respawn us.

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

    async def _async_connect(self) -> None:
        """One connect attempt: resolve BLEDevice, open GATT, post-connect reads."""
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
                    "cache; will retry",
                    self.address,
                )
                return

            # Clear any stale connection objects bleak might still hold.
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

            # Subscribe to RX before sending anything.
            try:
                await client.start_notify(CHAR_RX_DATA, self._on_rx_data)
                await client.start_notify(CHAR_RX_FINAL, self._on_rx_final)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "AIPrimeHub %s: RX subscribe failed: %s", self.address, err
                )
                await self._async_disconnect()
                return

            # Mark connected BEFORE post-connect reads so they see
            # ble_connected=True. Availability + state dispatch happens
            # AFTER reads land, so HA doesn't briefly show everything-missing.
            self.state.ble_connected = True
            self._reconnect_backoff = DEFAULT_RECONNECT_BACKOFF_INITIAL_S

            # FSCI smoke test: GET ATTR_SERIAL(3). Same query as Day 3.
            await self._read_fsci_serial()

            # Standard 0x180A device info — best-effort, never aborts setup.
            await self._read_device_info(client)

            self._notify_availability_changed()
            self._notify_state_changed()
            _LOGGER.info(
                "AIPrimeHub %s: connected; serial=%s manufacturer=%s "
                "model=%s firmware=%s",
                self.address,
                self.state.serial,
                self.state.manufacturer,
                self.state.model_number,
                self.state.firmware_revision,
            )

    async def _async_disconnect(self) -> None:
        """Drop the GATT link, cancel pending requests, mark unavailable."""
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
        """bleak-side callback when the GATT link drops."""
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
            # Fresh epoch — start backoff at the initial value.
            self._reconnect_backoff = DEFAULT_RECONNECT_BACKOFF_INITIAL_S
            self._spawn_connect_task()

    # --- RX path (BLE notifications) -------------------------------------

    def _on_rx_data(self, _ch: Any, data: bytearray) -> None:
        """Notification handler for RX_DATA — accumulate the chunk."""
        self._rx_buffer.extend(data)

    def _on_rx_final(self, _ch: Any, data: bytearray) -> None:
        """RX_FINAL — flush the buffered frame and dispatch by msg_id."""
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
        """Write a frame to TX_DATA, wait for matching CONFIRM.

        Returns the CONFIRM frame bytes, or None on timeout / disconnect /
        write failure.
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
        """Round-trip GET ATTR_SERIAL(3) to populate state.serial and prove
        the FSCI dispatch wiring works end-to-end.
        """
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
        """Best-effort 0x180A read; failures don't abort connect."""
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

    # --- Passive advertisement handler -----------------------------------

    @callback
    def _handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Track RSSI from advertisements — independent of GATT state."""
        if self.state.rssi != service_info.rssi:
            self.state.rssi = service_info.rssi
            self._notify_state_changed()

    # --- Public control surface (stubs — PR-3 implements writes) ---------

    async def async_set_channel(self, channel_id: int, value_device: int) -> None:
        """STUB: real FSCI write lands in PR-3."""
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
            "AIPrimeHub %s: STUB set channel 0x%02X -> %d (PR-3 will FSCI-write)",
            self.address,
            channel_id,
            clamped,
        )
        self._notify_state_changed()

    async def async_set_power(self, *, on: bool) -> None:
        """STUB: PR-3 implements via per-channel writes."""
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
            "AIPrimeHub %s: STUB set power=%s (PR-3 will FSCI-write)",
            self.address,
            on,
        )
        self._notify_state_changed()

    # --- Read helpers used by entities -----------------------------------

    def is_on(self) -> bool:
        """Aggregate on/off: any LED channel above zero."""
        return any(
            cs.value_device > 0
            for cid, cs in self.state.channels.items()
            if cid != CHANNEL_ID_FAN
        )

    def aggregate_brightness_device(self) -> int:
        """Aggregate brightness as the max of all LED channels (0-1000)."""
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
    """Convenience accessor used by platform setup functions."""
    domain_data: dict[str, Any] = hass.data[DOMAIN][entry.entry_id]
    return domain_data["hub"]
