"""Tests for the xTool S1 job store."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.xtool_s1.store import SavedJob, XToolS1JobStore

from .const import MOCK_SERIAL, MOCK_SERIAL_2


def _make_job_dict(title: str, serial: str | None = MOCK_SERIAL) -> dict:
    return SavedJob(
        title=title,
        description="test",
        material="Wood",
        thickness_mm=3.0,
        gcode="G0X10",
        saved_at="2026-04-10T00:00:00+00:00",
        serial_number=serial,
    ).to_dict()


def _patch_store(store, data):
    """Patch both async_load and async_save on the inner HA Store."""
    return (
        patch.object(
            store._store,
            "async_load",
            new_callable=AsyncMock,
            return_value=data,
        ),
        patch.object(
            store._store,
            "async_save",
            new_callable=AsyncMock,
        ),
    )


@pytest.mark.asyncio
async def test_migrate_legacy_keys(hass: HomeAssistant) -> None:
    """Old flat-title keys are migrated to serial/title composite keys."""
    store = XToolS1JobStore(hass)
    legacy_data = {
        "rect": _make_job_dict("rect", MOCK_SERIAL),
        "circle": _make_job_dict("circle", MOCK_SERIAL),
    }
    p_load, p_save = _patch_store(store, legacy_data)
    with p_load, p_save as mock_save:
        jobs = await store.async_load()

    assert f"{MOCK_SERIAL}/rect" in jobs
    assert f"{MOCK_SERIAL}/circle" in jobs
    assert "rect" not in jobs
    assert "circle" not in jobs

    mock_save.assert_awaited_once()
    saved_data = mock_save.call_args[0][0]
    assert f"{MOCK_SERIAL}/rect" in saved_data
    assert "rect" not in saved_data


@pytest.mark.asyncio
async def test_migrate_legacy_keys_null_serial(
    hass: HomeAssistant,
) -> None:
    """Legacy jobs without serial_number use 'unknown' as prefix."""
    store = XToolS1JobStore(hass)
    legacy_data = {"old_job": _make_job_dict("old_job", serial=None)}
    p_load, p_save = _patch_store(store, legacy_data)
    with p_load, p_save:
        jobs = await store.async_load()

    assert "unknown/old_job" in jobs


@pytest.mark.asyncio
async def test_no_migration_for_new_keys(hass: HomeAssistant) -> None:
    """Already-composite keys are not migrated again."""
    store = XToolS1JobStore(hass)
    new_data = {
        f"{MOCK_SERIAL}/rect": _make_job_dict("rect", MOCK_SERIAL),
    }
    p_load, p_save = _patch_store(store, new_data)
    with p_load, p_save as mock_save:
        jobs = await store.async_load()

    assert f"{MOCK_SERIAL}/rect" in jobs
    mock_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_and_get_job(hass: HomeAssistant) -> None:
    """Round-trip: save a job, then retrieve it by serial+title."""
    store = XToolS1JobStore(hass)
    p_load, p_save = _patch_store(store, {})
    with p_load, p_save:
        job = SavedJob(
            title="test",
            description="d",
            material="M",
            thickness_mm=1.0,
            gcode="G0",
            saved_at="2026-04-10T00:00:00+00:00",
            serial_number=MOCK_SERIAL,
        )
        await store.async_save_job(job)
        result = await store.async_get_job("test", MOCK_SERIAL)

    assert result is not None
    assert result.title == "test"
    assert result.serial_number == MOCK_SERIAL


@pytest.mark.asyncio
async def test_list_jobs_filter_by_serial(
    hass: HomeAssistant,
) -> None:
    """list_jobs can filter by serial_number."""
    store = XToolS1JobStore(hass)
    data = {
        f"{MOCK_SERIAL}/a": _make_job_dict("a", MOCK_SERIAL),
        f"{MOCK_SERIAL_2}/b": _make_job_dict("b", MOCK_SERIAL_2),
    }
    p_load, _p_save = _patch_store(store, data)
    with p_load:
        all_jobs = await store.async_list_jobs()
        filtered = await store.async_list_jobs(serial_number=MOCK_SERIAL)

    assert len(all_jobs) == 2
    assert len(filtered) == 1
    assert filtered[0]["title"] == "a"


@pytest.mark.asyncio
async def test_delete_job_by_serial(hass: HomeAssistant) -> None:
    """delete_job only removes the job for the specified serial."""
    store = XToolS1JobStore(hass)
    data = {
        f"{MOCK_SERIAL}/x": _make_job_dict("x", MOCK_SERIAL),
        f"{MOCK_SERIAL_2}/x": _make_job_dict("x", MOCK_SERIAL_2),
    }
    p_load, p_save = _patch_store(store, data)
    with p_load, p_save:
        result = await store.async_delete_job("x", MOCK_SERIAL)
        remaining = await store.async_list_jobs()

    assert result is True
    assert len(remaining) == 1
    assert remaining[0]["serial_number"] == MOCK_SERIAL_2
