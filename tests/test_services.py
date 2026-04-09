"""Tests for the xTool S1 job management services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.api import XToolS1ConnectionError
from custom_components.xtool_s1.const import DOMAIN
from custom_components.xtool_s1.services import (
    _extract_gcode_power_percent,
    async_register_services,
)

from .conftest import patch_ports
from .const import MOCK_SERIAL

SAMPLE_GCODE = """\
# date=2026_04_09
G90
# blockConfig={"powerFactor": 0.01, "isVector": true}
G0X10Y10
G1X20Y20 S10 F1680
"""


def _entry(host: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: host},
    )


# --- unit tests for helpers ---


def test_extract_power_percent() -> None:
    assert _extract_gcode_power_percent(SAMPLE_GCODE) == 1.0


def test_extract_power_percent_missing() -> None:
    assert _extract_gcode_power_percent("G0X10Y10") is None


def test_extract_power_percent_bad_value() -> None:
    assert _extract_gcode_power_percent('"powerFactor": "abc"') is None


@pytest.mark.asyncio
async def test_save_job_no_entry(hass: HomeAssistant) -> None:
    """save_job raises when no device is configured."""
    async_register_services(hass)
    with pytest.raises(HomeAssistantError, match="No xTool S1"):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "x", "description": "x", "material": "x", "thickness_mm": 1.0},
            blocking=True,
        )


# --- integration tests against FakeS1Server ---


@pytest.mark.asyncio
async def test_save_and_list_job(hass: HomeAssistant, fake_s1_server) -> None:
    """save_job downloads gcode and list_jobs returns it."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Mock the download to return known gcode
    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {
                "title": "test_rect",
                "description": "A test rectangle",
                "material": "Birch plywood",
                "thickness_mm": 3.0,
            },
            blocking=True,
        )

    result = await hass.services.async_call(
        DOMAIN,
        "list_jobs",
        {},
        blocking=True,
        return_response=True,
    )
    assert len(result["jobs"]) == 1
    job = result["jobs"][0]
    assert job["title"] == "test_rect"
    assert job["material"] == "Birch plywood"
    assert job["thickness_mm"] == 3.0
    assert job["power_percent"] == 1.0
    assert job["laser_module"] is not None


@pytest.mark.asyncio
async def test_save_job_download_failure(hass: HomeAssistant, fake_s1_server) -> None:
    """save_job raises HomeAssistantError when download fails."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.download_job",
            new_callable=AsyncMock,
            side_effect=XToolS1ConnectionError("offline"),
        ),
        pytest.raises(HomeAssistantError, match="download"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {
                "title": "fail",
                "description": "x",
                "material": "x",
                "thickness_mm": 1.0,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_happy_path(hass: HomeAssistant, fake_s1_server) -> None:
    """start_job uploads, connects WS, and runs the start sequence."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Save a job first
    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {
                "title": "rect",
                "description": "test",
                "material": "Wood",
                "thickness_mm": 3.0,
            },
            blocking=True,
        )

    # Start the job — mock upload + start sequence
    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.upload_job",
            new_callable=AsyncMock,
        ) as mock_upload,
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.start_job_sequence",
            new_callable=AsyncMock,
        ) as mock_start,
    ):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "rect",
                "confirm_material": "Wood",
                "confirm_thickness_mm": 3.0,
                "confirm_risk": True,
            },
            blocking=True,
        )
    mock_upload.assert_awaited_once()
    mock_start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_job_risk_not_confirmed(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """start_job refuses to run without risk confirmation."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Save a job
    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "r", "description": "x", "material": "X", "thickness_mm": 1.0},
            blocking=True,
        )

    with pytest.raises(HomeAssistantError, match="confirm_risk"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "r",
                "confirm_material": "X",
                "confirm_thickness_mm": 1.0,
                "confirm_risk": False,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_material_mismatch(hass: HomeAssistant, fake_s1_server) -> None:
    """start_job rejects mismatched material."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "m", "description": "x", "material": "Wood", "thickness_mm": 3.0},
            blocking=True,
        )

    with pytest.raises(HomeAssistantError, match="Material mismatch"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "m",
                "confirm_material": "Acrylic",
                "confirm_thickness_mm": 3.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_thickness_mismatch(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """start_job rejects mismatched thickness."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "t", "description": "x", "material": "W", "thickness_mm": 3.0},
            blocking=True,
        )

    with pytest.raises(HomeAssistantError, match="Thickness mismatch"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "t",
                "confirm_material": "W",
                "confirm_thickness_mm": 5.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_not_found(hass: HomeAssistant, fake_s1_server) -> None:
    """start_job raises for a non-existent job title."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="No saved job"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "nonexistent",
                "confirm_material": "x",
                "confirm_thickness_mm": 1.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_laser_module_mismatch(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """start_job rejects when the installed laser module changed."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Save with the current module (Diode 40 W from fixture)
    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "lm", "description": "x", "material": "W", "thickness_mm": 1.0},
            blocking=True,
        )

    # Now pretend the tool was swapped to IR 2 W
    coord = entry.runtime_data.coordinator
    from dataclasses import replace

    coord.async_set_updated_data(
        replace(coord.data, firmware_aux_1="V40.32.008.2122.01 B3")
    )

    with pytest.raises(HomeAssistantError, match="Laser module mismatch"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "lm",
                "confirm_material": "W",
                "confirm_thickness_mm": 1.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_upload_failure(hass: HomeAssistant, fake_s1_server) -> None:
    """start_job wraps upload errors in HomeAssistantError."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "u", "description": "x", "material": "W", "thickness_mm": 1.0},
            blocking=True,
        )

    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.upload_job",
            new_callable=AsyncMock,
            side_effect=XToolS1ConnectionError("timeout"),
        ),
        pytest.raises(HomeAssistantError, match="upload"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "u",
                "confirm_material": "W",
                "confirm_thickness_mm": 1.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_ws_connect_failure(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """start_job wraps WS connect errors in HomeAssistantError."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "wc", "description": "x", "material": "W", "thickness_mm": 1.0},
            blocking=True,
        )

    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.upload_job",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.connect",
            new_callable=AsyncMock,
            side_effect=XToolS1ConnectionError("refused"),
        ),
        pytest.raises(HomeAssistantError, match="Start sequence"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "wc",
                "confirm_material": "W",
                "confirm_thickness_mm": 1.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_start_sequence_failure(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """start_job wraps start-sequence errors in HomeAssistantError."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.download_job",
        new_callable=AsyncMock,
        return_value=SAMPLE_GCODE,
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "ss", "description": "x", "material": "W", "thickness_mm": 1.0},
            blocking=True,
        )

    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.upload_job",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.start_job_sequence",
            new_callable=AsyncMock,
            side_effect=XToolS1ConnectionError("ws dead"),
        ),
        pytest.raises(HomeAssistantError, match="Start sequence"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "ss",
                "confirm_material": "W",
                "confirm_thickness_mm": 1.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_skips_module_check_when_none(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """When laser_module is None (undetected at save), skip the check."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)

    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Save with a patched _current_laser_module returning None
    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.download_job",
            new_callable=AsyncMock,
            return_value=SAMPLE_GCODE,
        ),
        patch(
            "custom_components.xtool_s1.services._current_laser_module",
            return_value=None,
        ),
    ):
        await hass.services.async_call(
            DOMAIN,
            "save_job",
            {"title": "nl", "description": "x", "material": "W", "thickness_mm": 1.0},
            blocking=True,
        )

    # Start should succeed (no module check because saved module is None)
    with (
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.upload_job",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.start_job_sequence",
            new_callable=AsyncMock,
        ),
    ):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {
                "title": "nl",
                "confirm_material": "W",
                "confirm_thickness_mm": 1.0,
                "confirm_risk": True,
            },
            blocking=True,
        )


def test_current_laser_module_no_data() -> None:
    """_current_laser_module returns None when coordinator has no data."""
    from custom_components.xtool_s1.services import _current_laser_module

    entry = MagicMock()
    entry.runtime_data.coordinator.data = None
    assert _current_laser_module(entry) is None

    # Also test with data but no firmware_aux_1
    entry.runtime_data.coordinator.data = MagicMock(firmware_aux_1=None)
    assert _current_laser_module(entry) is None


def test_register_services_idempotent(hass: HomeAssistant) -> None:
    """Calling async_register_services twice does not raise."""
    async_register_services(hass)
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, "save_job")
    assert hass.services.has_service(DOMAIN, "start_job")
    assert hass.services.has_service(DOMAIN, "list_jobs")
