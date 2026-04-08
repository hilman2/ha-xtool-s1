"""Config flow for the xTool S1 integration."""

from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Network, ip_address
import logging
from typing import Any

from homeassistant.components import network as ha_network
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    DiscoveredDevice,
    NetworkTooLargeError,
    XToolS1Client,
    XToolS1ConnectionError,
    discover_devices,
    parse_network,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_NETWORK = "network"

STEP_MANUAL_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


def _normalise_host(raw: str) -> str:
    """Strip whitespace and protocol prefixes from a user-entered host."""
    cleaned = raw.strip()
    for prefix in ("http://", "https://", "ws://", "wss://"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.rstrip("/")


def _is_valid_host(host: str) -> bool:
    """Return True if ``host`` looks like an IPv4/IPv6 literal or a hostname."""
    if not host:
        return False
    try:
        ip_address(host)
        return True
    except ValueError:
        pass
    return all(
        part and all(c.isalnum() or c in "-_" for c in part) for part in host.split(".")
    )


class XToolS1ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the xTool S1."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._discovered: list[DiscoveredDevice] = []
        self._suggested_network: str = "192.168.1.0/24"

    # -- helpers ----------------------------------------------------------

    async def _suggest_network_default(self) -> str:
        """Pick a sensible default CIDR for the scan form."""
        try:
            adapters = await ha_network.async_get_adapters(self.hass)
        except Exception:
            return self._suggested_network
        for adapter in adapters:
            if not adapter.get("enabled"):
                continue
            for ip_info in adapter.get("ipv4", []):
                addr = ip_info.get("address")
                prefix = ip_info.get("network_prefix", 24)
                if not addr:
                    continue
                try:
                    ip_obj = ip_address(addr)
                except ValueError:
                    continue
                if ip_obj.is_loopback or ip_obj.is_link_local:
                    continue
                # Cap to /23 — anything larger and we ask the user to narrow it.
                effective_prefix = max(int(prefix), 23)
                return str(IPv4Network(f"{addr}/{effective_prefix}", strict=False))
        return self._suggested_network

    async def _probe(self, host: str) -> str:
        """Open a WebSocket, request status, return the device serial."""
        session = async_get_clientsession(self.hass)
        client = XToolS1Client(host, session)
        try:
            state = await client.probe_initial_state()
        finally:
            await client.disconnect()
        if state.serial_number is None:
            raise XToolS1ConnectionError("device returned no serial number")
        return state.serial_number

    async def _create_entry_for_host(self, host: str, serial: str) -> ConfigFlowResult:
        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(
            title=f"xTool S1 ({host})",
            data={CONF_HOST: host},
        )

    # -- step: method picker ---------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a menu — scan the local network or enter an IP manually."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["scan_network", "manual"],
        )

    # -- step: manual IP entry -------------------------------------------

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual IP entry — bypass network scan."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalise_host(user_input[CONF_HOST])
            if not _is_valid_host(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    serial = await self._probe(host)
                except XToolS1ConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error probing xTool S1")
                    errors["base"] = "unknown"
                else:
                    return await self._create_entry_for_host(host, serial)

        return self.async_show_form(
            step_id="manual",
            data_schema=STEP_MANUAL_DATA_SCHEMA,
            errors=errors,
        )

    # -- step: network scan ----------------------------------------------

    async def async_step_scan_network(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a CIDR range and run the scan."""
        errors: dict[str, str] = {}
        suggested = await self._suggest_network_default()

        if user_input is not None:
            try:
                network = parse_network(user_input[CONF_NETWORK])
            except NetworkTooLargeError:
                errors[CONF_NETWORK] = "network_too_large"
            except ValueError:
                errors[CONF_NETWORK] = "invalid_network"
            else:
                session = async_get_clientsession(self.hass)
                try:
                    self._discovered = await discover_devices(session, network)
                except Exception:
                    _LOGGER.exception("Network scan failed")
                    errors["base"] = "scan_failed"
                else:
                    if not self._discovered:
                        return self.async_abort(reason="no_devices_found")
                    return await self.async_step_scan_results()

        return self.async_show_form(
            step_id="scan_network",
            data_schema=vol.Schema(
                {vol.Required(CONF_NETWORK, default=suggested): str}
            ),
            errors=errors,
            description_placeholders={"suggested": suggested},
        )

    # -- step: choose discovered device ----------------------------------

    async def async_step_scan_results(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick from the discovered devices."""
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        host_to_device = {d.host: d for d in self._discovered}
        choices = {
            d.host: f"{d.host}  •  serial {d.serial_number}" for d in self._discovered
        }

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            device = host_to_device.get(host)
            if device is None:
                errors["base"] = "unknown"
            else:
                return await self._create_entry_for_host(
                    device.host, device.serial_number
                )

        return self.async_show_form(
            step_id="scan_results",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=next(iter(choices))): vol.In(choices)}
            ),
            errors=errors,
            description_placeholders={"count": str(len(self._discovered))},
        )

    # -- reconfigure ------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to update the device's IP address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalise_host(user_input[CONF_HOST])
            if not _is_valid_host(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    serial = await self._probe(host)
                except XToolS1ConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error probing xTool S1")
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(serial)
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_HOST: host},
                    )

        current: Mapping[str, Any] = entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str}
            ),
            errors=errors,
        )
