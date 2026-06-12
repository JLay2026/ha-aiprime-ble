from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .aiprime_hub import AIPrimeHub
from .const import (
    ATTR_DEPLOY_PROFILE_NAME,
    DOMAIN,
    PLATFORMS,
    SERVICE_DEPLOY_PROFILE,
)
from .schedule_deploy import async_deploy_profile

_DEPLOY_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEPLOY_PROFILE_NAME): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)


def _iter_entry_data(hass: HomeAssistant, entry_id: str | None) -> list[dict[str, Any]]:
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    out: list[dict[str, Any]] = []
    for eid, entry_data in domain_data.items():
        if not isinstance(entry_data, dict) or "hub" not in entry_data:
            continue
        if entry_id and eid != entry_id:
            continue
        out.append(entry_data)
    return out


async def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_DEPLOY_PROFILE):
        return

    async def _handle_deploy_profile(call: ServiceCall) -> None:
        name = call.data[ATTR_DEPLOY_PROFILE_NAME]
        entry_id = call.data.get("entry_id")
        targets = _iter_entry_data(hass, entry_id)
        if not targets:
            raise HomeAssistantError(
                "deploy_profile: no AI Prime device available"
                + (f" for entry_id {entry_id}" if entry_id else "")
            )
        failures: list[str] = []
        for entry_data in targets:
            ok = await async_deploy_profile(hass, entry_data, name)
            if not ok:
                failures.append(entry_data["hub"].address)
        if failures:
            raise HomeAssistantError(
                f"deploy_profile '{name}' failed for: {', '.join(failures)}"
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DEPLOY_PROFILE,
        _handle_deploy_profile,
        schema=_DEPLOY_PROFILE_SCHEMA,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an AI Prime BLE config entry."""
    hub = AIPrimeHub(hass, entry)
    await hub.async_setup()

    domain_data = hass.data.setdefault(DOMAIN, {})
    entry_data: dict[str, Any] = {
        "hub": hub,
        "selected_profile": None,
        "active_profile": None,
    }
    domain_data[entry.entry_id] = entry_data
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await _async_register_services(hass)

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry, tearing down platforms and the hub."""
    unload_ok = True
    if PLATFORMS:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    domain_data = hass.data.get(DOMAIN)
    entry_data: dict[str, Any] | None = None
    if domain_data is not None:
        entry_data = domain_data.pop(entry.entry_id, None)

    hub: AIPrimeHub | None = None
    if entry_data:
        hub = entry_data.get("hub")

    if hub:
        await hub.async_unload()

    if domain_data is not None and not domain_data:
        hass.data.pop(DOMAIN, None)
        if hass.services.has_service(DOMAIN, SERVICE_DEPLOY_PROFILE):
            hass.services.async_remove(DOMAIN, SERVICE_DEPLOY_PROFILE)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry — used when options change."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
