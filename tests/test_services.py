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
    _extract_gcode_speed,
    async_register_services,
)

from .conftest import patch_ports
from .const import MOCK_SERIAL

SAMPLE_GCODE = """\
# date=2026_04_09
G90
M109 S1
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


def test_extract_speed() -> None:
    assert _extract_gcode_speed(SAMPLE_GCODE) == 28.0  # 1680 mm/min / 60


def test_extract_speed_missing() -> None:
    assert _extract_gcode_speed("G0X10") is None


def test_extract_mode_cut() -> None:
    from custom_components.xtool_s1.services import _extract_gcode_mode

    assert _extract_gcode_mode("M109 S1\nG0X10") == "cut"


def test_extract_mode_frame() -> None:
    from custom_components.xtool_s1.services import _extract_gcode_mode

    assert _extract_gcode_mode("M109 S0\nG0X10") == "frame"


def test_extract_mode_missing() -> None:
    from custom_components.xtool_s1.services import _extract_gcode_mode

    assert _extract_gcode_mode("G0X10") is None


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


def test_current_laser_module_no_data() -> None:
    """_current_laser_module returns None when coordinator has no data."""
    from custom_components.xtool_s1.services import _current_laser_module

    entry = MagicMock()
    entry.runtime_data.coordinator.data = None
    assert _current_laser_module(entry) is None

    entry.runtime_data.coordinator.data = MagicMock(firmware_aux_1=None)
    assert _current_laser_module(entry) is None


# --- integration tests ---


async def _setup_and_save(hass, server, title="rect", material="Wood", thickness=3.0):
    """Helper: set up integration + save a job."""
    entry = _entry(server.host)
    entry.add_to_hass(hass)
    with patch_ports(server):
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
            {
                "title": title,
                "description": "test",
                "material": material,
                "thickness_mm": thickness,
            },
            blocking=True,
        )
    return entry


@pytest.mark.asyncio
async def test_save_and_list_job(hass: HomeAssistant, fake_s1_server) -> None:
    """save_job + list_jobs round-trip."""
    await _setup_and_save(hass, fake_s1_server, title="test_rect", material="Birch")

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
    assert job["material"] == "Birch"
    assert job["power_percent"] == 1.0
    assert job["speed_mm_per_s"] == 28.0
    assert job["laser_mode"] == "cut"


@pytest.mark.asyncio
async def test_save_job_download_failure(hass: HomeAssistant, fake_s1_server) -> None:
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
            {"title": "f", "description": "x", "material": "x", "thickness_mm": 1.0},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_happy_path(hass: HomeAssistant, fake_s1_server) -> None:
    await _setup_and_save(hass, fake_s1_server)

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
            {"title": "rect", "confirm": True},
            blocking=True,
        )
    mock_upload.assert_awaited_once()
    mock_start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_job_not_confirmed(hass: HomeAssistant, fake_s1_server) -> None:
    await _setup_and_save(hass, fake_s1_server)

    with pytest.raises(HomeAssistantError, match="confirm"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {"title": "rect", "confirm": False},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_not_found(hass: HomeAssistant, fake_s1_server) -> None:
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="No saved job"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {"title": "nonexistent", "confirm": True},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_laser_module_mismatch(
    hass: HomeAssistant, fake_s1_server
) -> None:
    entry = await _setup_and_save(hass, fake_s1_server)

    # Swap tool to IR 2 W
    from dataclasses import replace

    coord = entry.runtime_data.coordinator
    coord.async_set_updated_data(
        replace(coord.data, firmware_aux_1="V40.32.008.2122.01 B3")
    )

    with pytest.raises(HomeAssistantError, match="Laser module mismatch"):
        await hass.services.async_call(
            DOMAIN,
            "start_job",
            {"title": "rect", "confirm": True},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_skips_module_check_when_none(
    hass: HomeAssistant, fake_s1_server
) -> None:
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

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
            {"title": "nl", "confirm": True},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_upload_failure(hass: HomeAssistant, fake_s1_server) -> None:
    await _setup_and_save(hass, fake_s1_server)

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
            {"title": "rect", "confirm": True},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_ws_connect_failure(
    hass: HomeAssistant, fake_s1_server
) -> None:
    await _setup_and_save(hass, fake_s1_server)

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
            {"title": "rect", "confirm": True},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_start_job_start_sequence_failure(
    hass: HomeAssistant, fake_s1_server
) -> None:
    await _setup_and_save(hass, fake_s1_server)

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
            {"title": "rect", "confirm": True},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_delete_job(hass: HomeAssistant, fake_s1_server) -> None:
    await _setup_and_save(hass, fake_s1_server)

    await hass.services.async_call(
        DOMAIN,
        "delete_job",
        {"title": "rect"},
        blocking=True,
    )
    result = await hass.services.async_call(
        DOMAIN,
        "list_jobs",
        {},
        blocking=True,
        return_response=True,
    )
    assert len(result["jobs"]) == 0


@pytest.mark.asyncio
async def test_delete_job_not_found(hass: HomeAssistant, fake_s1_server) -> None:
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="No saved job"):
        await hass.services.async_call(
            DOMAIN,
            "delete_job",
            {"title": "nope"},
            blocking=True,
        )


def test_register_services_idempotent(hass: HomeAssistant) -> None:
    async_register_services(hass)
    async_register_services(hass)
    assert hass.services.has_service(DOMAIN, "save_job")
    assert hass.services.has_service(DOMAIN, "start_job")
    assert hass.services.has_service(DOMAIN, "delete_job")
    assert hass.services.has_service(DOMAIN, "list_jobs")
