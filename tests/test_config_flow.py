"""Tests for the xtool_s1 config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.api import (
    DiscoveredDevice,
    XToolS1ConnectionError,
)
from custom_components.xtool_s1.const import DOMAIN

from .const import MOCK_HOST, MOCK_SERIAL

# --- helpers ---------------------------------------------------------------


def _patch_probe(
    *, serial: str | None = MOCK_SERIAL, side_effect: Exception | None = None
):
    """Patch ``XToolS1ConfigFlow._probe`` to skip the real WebSocket call."""
    target = "custom_components.xtool_s1.config_flow.XToolS1ConfigFlow._probe"
    if side_effect is not None:
        return patch(target, side_effect=side_effect)
    return patch(target, return_value=serial)


# --- step user (menu) ------------------------------------------------------


async def test_user_menu_shows_two_options(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    assert set(result["menu_options"]) == {"scan_network", "manual"}


# --- manual flow happy path ------------------------------------------------


async def test_manual_flow_happy_path(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"

    with _patch_probe():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: MOCK_HOST}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: MOCK_HOST}
    assert result["title"] == f"xTool S1 ({MOCK_HOST})"
    assert result["result"].unique_id == MOCK_SERIAL


async def test_manual_flow_strips_protocol_prefix(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with _patch_probe():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: f"http://{MOCK_HOST}/"}
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == MOCK_HOST


async def test_manual_flow_invalid_host(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "not a host!"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}


async def test_manual_flow_cannot_connect(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with _patch_probe(side_effect=XToolS1ConnectionError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: MOCK_HOST}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_flow_unknown_error(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with _patch_probe(side_effect=RuntimeError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: MOCK_HOST}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_manual_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    with _patch_probe():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: MOCK_HOST}
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# --- scan flow -------------------------------------------------------------


async def test_scan_flow_happy_path(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan_network"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "scan_network"

    discovered = [
        DiscoveredDevice(
            host=MOCK_HOST, serial_number=MOCK_SERIAL, firmware_version="1.2.3"
        )
    ]
    with patch(
        "custom_components.xtool_s1.config_flow.discover_devices",
        return_value=discovered,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "scan_results"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: MOCK_HOST}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: MOCK_HOST}
    assert result["result"].unique_id == MOCK_SERIAL


async def test_scan_flow_no_devices_aborts(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan_network"}
    )
    with patch(
        "custom_components.xtool_s1.config_flow.discover_devices",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_scan_flow_invalid_network(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan_network"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"network": "not a cidr"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"network": "invalid_network"}


async def test_scan_flow_network_too_large(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan_network"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"network": "10.0.0.0/8"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"network": "network_too_large"}


async def test_scan_flow_scan_failed(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan_network"}
    )
    with patch(
        "custom_components.xtool_s1.config_flow.discover_devices",
        side_effect=RuntimeError("network died"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"network": "192.168.1.0/24"}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "scan_failed"}


# --- reconfigure -----------------------------------------------------------


async def test_reconfigure_flow_updates_host(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    new_host = "192.168.4.99"
    with _patch_probe():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: new_host}
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == new_host


async def test_reconfigure_flow_wrong_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    with _patch_probe(serial="DIFFERENT-SERIAL"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.4.99"}
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


async def test_reconfigure_flow_invalid_host(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "not a host!"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}


async def test_reconfigure_flow_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    with _patch_probe(side_effect=XToolS1ConnectionError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.4.99"}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_flow_unknown_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    with _patch_probe(side_effect=RuntimeError("boom")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.4.99"}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


# --- network suggestion ----------------------------------------------------


async def test_suggest_network_default_from_adapter(hass: HomeAssistant) -> None:
    """The scan_network form pre-fills the default with the adapter's CIDR."""
    fake_adapters = [
        {
            "enabled": True,
            "ipv4": [{"address": "192.168.42.7", "network_prefix": 24}],
        }
    ]
    with patch(
        "custom_components.xtool_s1.config_flow.ha_network.async_get_adapters",
        return_value=fake_adapters,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan_network"}
        )
    assert result["type"] == FlowResultType.FORM
    # The voluptuous schema's default is the suggested CIDR.
    schema = result["data_schema"].schema
    network_default = next((k.default() for k in schema if str(k) == "network"), None)
    assert network_default == "192.168.42.0/24"


async def test_suggest_network_default_skips_loopback(hass: HomeAssistant) -> None:
    fake_adapters = [
        {
            "enabled": True,
            "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}],
        },
        {
            "enabled": True,
            "ipv4": [{"address": "192.168.7.1", "network_prefix": 24}],
        },
    ]
    with patch(
        "custom_components.xtool_s1.config_flow.ha_network.async_get_adapters",
        return_value=fake_adapters,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan_network"}
        )
    schema = result["data_schema"].schema
    network_default = next((k.default() for k in schema if str(k) == "network"), None)
    assert network_default == "192.168.7.0/24"


async def test_suggest_network_default_handles_adapter_error(
    hass: HomeAssistant,
) -> None:
    """When async_get_adapters raises, the default falls back to 192.168.1.0/24."""
    with patch(
        "custom_components.xtool_s1.config_flow.ha_network.async_get_adapters",
        side_effect=RuntimeError("adapter boom"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan_network"}
        )
    schema = result["data_schema"].schema
    network_default = next((k.default() for k in schema if str(k) == "network"), None)
    assert network_default == "192.168.1.0/24"


async def test_suggest_network_skips_disabled_adapter(hass: HomeAssistant) -> None:
    fake_adapters = [
        {
            "enabled": False,
            "ipv4": [{"address": "10.0.0.5", "network_prefix": 24}],
        },
        {
            "enabled": True,
            "ipv4": [{"address": "10.20.30.40", "network_prefix": 24}],
        },
    ]
    with patch(
        "custom_components.xtool_s1.config_flow.ha_network.async_get_adapters",
        return_value=fake_adapters,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan_network"}
        )
    schema = result["data_schema"].schema
    network_default = next((k.default() for k in schema if str(k) == "network"), None)
    assert network_default == "10.20.30.0/24"


# --- probe internals -------------------------------------------------------


async def test_probe_helper_disconnects_on_no_serial(hass: HomeAssistant) -> None:
    """The internal _probe helper raises if the device returns no serial."""
    from custom_components.xtool_s1.api import XToolS1State
    from custom_components.xtool_s1.config_flow import XToolS1ConfigFlow

    flow = XToolS1ConfigFlow()
    flow.hass = hass
    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.probe_initial_state",
            return_value=XToolS1State(),  # serial_number is None
        ),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.disconnect",
            return_value=None,
        ),
        pytest.raises(XToolS1ConnectionError),
    ):
        await flow._probe("127.0.0.1")


def test_is_valid_host_helpers() -> None:
    """Edge cases for the host validator."""
    from custom_components.xtool_s1.config_flow import _is_valid_host

    assert _is_valid_host("") is False
    assert _is_valid_host("192.168.1.1") is True
    assert _is_valid_host("my-host.local") is True
    assert _is_valid_host("bad host") is False


async def test_scan_results_aborts_when_called_without_discovery(
    hass: HomeAssistant,
) -> None:
    """Hitting scan_results with an empty _discovered list aborts immediately."""
    from custom_components.xtool_s1.config_flow import XToolS1ConfigFlow

    flow = XToolS1ConfigFlow()
    flow.hass = hass
    result = await flow.async_step_scan_results(None)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_scan_results_unknown_host_sets_error(hass: HomeAssistant) -> None:
    """Submitting a host the bypass-validator lets through hits the unknown branch."""
    from custom_components.xtool_s1.api import DiscoveredDevice
    from custom_components.xtool_s1.config_flow import XToolS1ConfigFlow

    flow = XToolS1ConfigFlow()
    flow.hass = hass
    flow._discovered = [
        DiscoveredDevice(host="10.0.0.1", serial_number="A", firmware_version="1")
    ]
    # Direct invocation bypasses voluptuous, so the dict-lookup miss path runs.
    result = await flow.async_step_scan_results({CONF_HOST: "10.0.0.99"})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


# --- network suggestion edge cases -----------------------------------------


async def test_suggest_network_skips_entry_without_address(
    hass: HomeAssistant,
) -> None:
    fake_adapters = [
        {"enabled": True, "ipv4": [{"network_prefix": 24}]},  # no 'address'
        {"enabled": True, "ipv4": [{"address": "10.5.5.5", "network_prefix": 24}]},
    ]
    with patch(
        "custom_components.xtool_s1.config_flow.ha_network.async_get_adapters",
        return_value=fake_adapters,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan_network"}
        )
    schema = result["data_schema"].schema
    network_default = next((k.default() for k in schema if str(k) == "network"), None)
    assert network_default == "10.5.5.0/24"


async def test_suggest_network_skips_invalid_address(hass: HomeAssistant) -> None:
    fake_adapters = [
        {"enabled": True, "ipv4": [{"address": "not.an.ip", "network_prefix": 24}]},
        {"enabled": True, "ipv4": [{"address": "10.6.6.6", "network_prefix": 24}]},
    ]
    with patch(
        "custom_components.xtool_s1.config_flow.ha_network.async_get_adapters",
        return_value=fake_adapters,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan_network"}
        )
    schema = result["data_schema"].schema
    network_default = next((k.default() for k in schema if str(k) == "network"), None)
    assert network_default == "10.6.6.0/24"


async def test_suggest_network_falls_back_when_only_loopback(
    hass: HomeAssistant,
) -> None:
    """If every adapter is loopback/link-local, the hard-coded default kicks in."""
    fake_adapters = [
        {"enabled": True, "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}]},
        {"enabled": True, "ipv4": [{"address": "169.254.1.1", "network_prefix": 16}]},
    ]
    with patch(
        "custom_components.xtool_s1.config_flow.ha_network.async_get_adapters",
        return_value=fake_adapters,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan_network"}
        )
    schema = result["data_schema"].schema
    network_default = next((k.default() for k in schema if str(k) == "network"), None)
    assert network_default == "192.168.1.0/24"


async def test_probe_helper_returns_serial(hass: HomeAssistant) -> None:
    """The internal _probe helper returns the serial on success."""
    from custom_components.xtool_s1.api import XToolS1State
    from custom_components.xtool_s1.config_flow import XToolS1ConfigFlow

    flow = XToolS1ConfigFlow()
    flow.hass = hass
    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.probe_initial_state",
            return_value=XToolS1State(serial_number="ABCDEF"),
        ),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.disconnect",
            return_value=None,
        ),
    ):
        result = await flow._probe("127.0.0.1")
    assert result == "ABCDEF"
