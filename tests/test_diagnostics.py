"""Tests for the xtool_s1 diagnostics export."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.const import DOMAIN
from custom_components.xtool_s1.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import patch_ports
from .const import MOCK_SERIAL


@pytest.mark.asyncio
async def test_diagnostics_redact_sensitive_fields(
    hass: HomeAssistant, fake_s1_server
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: fake_s1_server.host},
    )
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Host is redacted everywhere it can leak.
    assert diag["entry"]["data"][CONF_HOST] == "**REDACTED**"
    assert diag["client"]["host"] == "**REDACTED**"
    assert diag["state"]["serial_number"] == "**REDACTED**"

    # Non-sensitive metadata is preserved.
    assert diag["client"]["connected"] is True
    assert diag["coordinator"]["last_update_success"] is True
    assert diag["coordinator"]["update_interval_seconds"] == 30.0
    # The state snapshot exists and contains the work_state_raw field.
    assert "work_state_raw" in diag["state"]
