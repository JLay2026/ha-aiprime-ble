from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .aiprime_hub import AIPrimeHub
from .const import DOMAIN, PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an AI Prime BLE config entry."""
    hub = AIPrimeHub(hass, entry)
    await hub.async_setup()

    domain_data = hass.data.setdefault(DOMAIN, {})
    entry_data: dict[str, Any] = {"hub": hub}
    domain_data[entry.entry_id] = entry_data
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

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

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry — used when options change."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
