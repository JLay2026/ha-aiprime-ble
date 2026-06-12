"""PR-6: native .aip schedule deploy + read-back orchestration.

Implemented as free functions (not hub methods) so the BLE hub stays exactly
as shipped — this module drives the hub's existing FSCI plumbing
(`hub._send_request`, `hub._codec`, `hub._async_prime`) and the pure codec in
`protocol.schedule`.

A deploy mirrors myAI: re-read the schedule/config (priming), then send three
FSCI SET frames on CHAR_TX_DATA — SET attr 500 (the schedule) + attr 511, then
two SET attr 510 commits. Frame bytes are produced by the byte-exact codec
(`build_schedule_set_frame` / `build_commit_frame`, gate-verified); only the
msg_id is allocated from the hub's codec so RX dispatch matches.

Read-back: GET attr 500, decode the active points, and match them to a known
.aip in <config>/aiprime/profiles/.

`entry_data` is the per-entry dict stored in hass.data[DOMAIN][entry_id]:
``{"hub": AIPrimeHub, "selected_profile": str|None, "active_profile": str|None}``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import ATTR_SCHEDULE, MYAI_WRITE_PRIME_GROUP, PROFILES_SUBDIR
from .protocol import (
    STATUS_SUCCESS,
    parse_get_attribute_payload,
    parse_response_status,
    status_name,
)
from .protocol.aip import AipParseError, Profile, parse_aip
from .protocol.schedule import (
    COMMIT_VALUES,
    build_commit_frame,
    build_schedule_set_frame,
    match_active_profile,
    parse_schedule_read,
    profile_to_points,
)

_LOGGER = logging.getLogger(__name__)


# --- Filesystem (sync; call via async_add_executor_job) --------------------

def profiles_dir(hass: HomeAssistant) -> str:
    return hass.config.path(PROFILES_SUBDIR)


def discover_profiles(hass: HomeAssistant) -> list[str]:
    """Sorted .aip profile names (file stems) in the profiles dir."""
    directory = profiles_dir(hass)
    try:
        entries = os.listdir(directory)
    except FileNotFoundError:
        return []
    return sorted(
        name[:-4]
        for name in entries
        if name.lower().endswith(".aip")
        and os.path.isfile(os.path.join(directory, name))
    )


def load_profiles(hass: HomeAssistant) -> dict[str, Profile]:
    """Parse every .aip into a {name: Profile} map (best-effort)."""
    directory = profiles_dir(hass)
    out: dict[str, Profile] = {}
    try:
        entries = os.listdir(directory)
    except FileNotFoundError:
        return out
    for name in entries:
        if not name.lower().endswith(".aip"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            out[name[:-4]] = parse_aip(path)
        except (AipParseError, OSError) as err:
            _LOGGER.warning("Failed to parse profile %s: %s", name, err)
    return out


def load_profile(hass: HomeAssistant, name: str) -> Profile | None:
    path = os.path.join(profiles_dir(hass), f"{name}.aip")
    if not os.path.isfile(path):
        return None
    return parse_aip(path)


async def async_list_profiles(hass: HomeAssistant) -> list[str]:
    return await hass.async_add_executor_job(discover_profiles, hass)


# --- Deploy + read-back ----------------------------------------------------

def _confirm_ok(reply: bytes | None, label: str, address: str) -> bool:
    if reply is None:
        _LOGGER.warning("AIPrime %s: %s — no reply (retries exhausted)", address, label)
        return False
    status = parse_response_status(reply)
    if status != STATUS_SUCCESS:
        _LOGGER.warning(
            "AIPrime %s: %s — CONFIRM status=%s",
            address, label,
            status_name(status) if status >= 0 else "MalformedFrame",
        )
        return False
    return True


async def async_deploy_profile(
    hass: HomeAssistant, entry_data: dict[str, Any], name: str
) -> bool:
    """Deploy the named .aip schedule to the device. Returns True on success."""
    hub = entry_data["hub"]
    if not hub.state.ble_connected:
        _LOGGER.warning("AIPrime %s: deploy '%s' skipped — not connected", hub.address, name)
        return False
    try:
        profile = await hass.async_add_executor_job(load_profile, hass, name)
    except AipParseError as err:
        _LOGGER.error("AIPrime %s: deploy '%s' — parse error: %s", hub.address, name, err)
        return False
    if profile is None:
        _LOGGER.error("AIPrime %s: deploy '%s' — profile not found", hub.address, name)
        return False
    points = profile_to_points(profile)
    if not points:
        _LOGGER.error("AIPrime %s: deploy '%s' — no active points", hub.address, name)
        return False

    # Mirror myAI: re-read schedule/config before writing it back.
    await hub._async_prime((MYAI_WRITE_PRIME_GROUP,), "pre-deploy")

    codec = hub._codec

    def _schedule_builder():
        msg_id = codec._next_msg_id()
        return msg_id, build_schedule_set_frame(points, msg_id)

    reply = await hub._send_request(_schedule_builder)
    if not _confirm_ok(reply, f"deploy '{name}' schedule SET (attr 500)", hub.address):
        return False

    for value in COMMIT_VALUES:
        def _commit_builder(v=value):
            msg_id = codec._next_msg_id()
            return msg_id, build_commit_frame(v, msg_id)

        reply = await hub._send_request(_commit_builder)
        if not _confirm_ok(reply, f"deploy '{name}' commit (attr 510)", hub.address):
            return False

    _LOGGER.info(
        "AIPrime %s: deployed schedule profile '%s' (%d active points)",
        hub.address, name, len(points),
    )
    entry_data["active_profile"] = name  # optimistic; confirmed by read-back
    async_dispatcher_send(hass, hub.signal_state_updated)
    await async_read_active_profile(hass, entry_data)
    return True


async def async_read_active_profile(
    hass: HomeAssistant, entry_data: dict[str, Any]
) -> str | None:
    """GET attr 500, decode + match to a known profile; store in entry_data."""
    hub = entry_data["hub"]
    if not hub.state.ble_connected:
        return entry_data.get("active_profile")
    reply = await hub._send_request(
        lambda: hub._codec.build_get_attribute(ATTR_SCHEDULE, instance=0, count=0xFF)
    )
    if reply is None:
        return entry_data.get("active_profile")
    status = parse_response_status(reply)
    if status != STATUS_SUCCESS:
        _LOGGER.debug(
            "AIPrime %s: attr-500 read CONFIRM status=%s",
            hub.address,
            status_name(status) if status >= 0 else "MalformedFrame",
        )
        return entry_data.get("active_profile")
    values = parse_get_attribute_payload(reply, ATTR_SCHEDULE)
    points = parse_schedule_read(values)
    profiles = await hass.async_add_executor_job(load_profiles, hass)
    matched = match_active_profile(points, profiles) if points else None
    new_active = matched if matched else ("Unknown" if points else None)
    if entry_data.get("active_profile") != new_active:
        entry_data["active_profile"] = new_active
        async_dispatcher_send(hass, hub.signal_state_updated)
    _LOGGER.debug(
        "AIPrime %s: active schedule = %s (%d active points)",
        hub.address, new_active, len(points),
    )
    return new_active
