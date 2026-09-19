"""System health: can this Home Assistant reach the API at all."""

from __future__ import annotations

from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


@callback
def async_register(
    hass: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    entries = hass.config_entries.async_entries(DOMAIN)
    base_url = (
        entries[0].data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
        if entries
        else DEFAULT_BASE_URL
    )
    return {
        "backend": base_url,
        "reachable": await system_health.async_check_can_reach_url(hass, base_url),
    }
