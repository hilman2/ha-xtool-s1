"""Service handlers for the xTool S1 integration.

Exposes four services for job management:

* ``xtool_s1.save_job`` — download + persist with metadata
* ``xtool_s1.start_job`` — upload + WS start sequence (confirm required)
* ``xtool_s1.delete_job`` — remove a saved job
* ``xtool_s1.list_jobs`` — return all saved jobs
"""

from __future__ import annotations

import re
import uuid

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .api import XToolS1ConnectionError
from .const import DOMAIN, TOOL_FIRMWARE_NAMES
from .coordinator import XToolS1ConfigEntry
from .store import SavedJob, XToolS1JobStore

# --- gcode metadata extraction ---

_POWER_FACTOR_RE = re.compile(r'"powerFactor"\s*:\s*([\d.]+)')
_FEED_RE = re.compile(r"G1[^;\n]*F(\d+)")
_M109_RE = re.compile(r"^M109\s+S(\d+)", re.MULTILINE)


def _extract_gcode_power_percent(gcode: str) -> float | None:
    match = _POWER_FACTOR_RE.search(gcode)
    if match:
        try:
            return round(float(match.group(1)) * 100, 2)
        except ValueError:  # pragma: no cover
            pass
    return None


def _extract_gcode_speed(gcode: str) -> float | None:
    """Extract the max cutting feed rate in mm/s from G1 F<mm/min> commands."""
    max_feed = 0.0
    for match in _FEED_RE.finditer(gcode):
        try:
            max_feed = max(max_feed, float(match.group(1)))
        except ValueError:  # pragma: no cover
            continue
    return round(max_feed / 60.0, 1) if max_feed > 0 else None


def _extract_gcode_mode(gcode: str) -> str | None:
    match = _M109_RE.search(gcode)
    if match:
        return "cut" if match.group(1) == "1" else "frame"
    return None


# --- helpers ---


def _current_laser_module(entry: XToolS1ConfigEntry) -> str | None:
    data = entry.runtime_data.coordinator.data
    if data is None or data.firmware_aux_1 is None:
        return None
    return TOOL_FIRMWARE_NAMES.get(data.firmware_aux_1, "Unknown")


def _get_entry(hass: HomeAssistant) -> XToolS1ConfigEntry:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No xTool S1 device configured")
    return entries[0]  # type: ignore[return-value]


def _get_store(hass: HomeAssistant) -> XToolS1JobStore:
    key = f"{DOMAIN}_job_store"
    if key not in hass.data:
        hass.data[key] = XToolS1JobStore(hass)
    return hass.data[key]


# --- service handlers ---


async def async_save_job(call: ServiceCall) -> None:
    """Download current gcode from laser and persist with metadata."""
    hass = call.hass
    entry = _get_entry(hass)
    client = entry.runtime_data.client
    store = _get_store(hass)

    try:
        gcode = await client.download_job()
    except XToolS1ConnectionError as err:
        raise HomeAssistantError(f"Failed to download gcode from laser: {err}") from err

    state = entry.runtime_data.coordinator.data
    job = SavedJob(
        title=call.data["title"],
        description=call.data["description"],
        material=call.data["material"],
        thickness_mm=float(call.data["thickness_mm"]),
        gcode=gcode,
        saved_at=XToolS1JobStore.now_iso(),
        serial_number=state.serial_number if state else None,
        laser_module=_current_laser_module(entry),
        power_percent=_extract_gcode_power_percent(gcode),
        speed_mm_per_s=_extract_gcode_speed(gcode),
        laser_mode=_extract_gcode_mode(gcode),
    )
    await store.async_save_job(job)


async def async_start_job(call: ServiceCall) -> None:
    """Upload a saved job and trigger the WS start sequence.

    The caller must set ``confirm: true`` to acknowledge they have
    reviewed the job properties. The frontend shows material, thickness,
    power, speed and laser module before asking for confirmation.
    """
    hass = call.hass
    entry = _get_entry(hass)
    client = entry.runtime_data.client
    store = _get_store(hass)

    title = call.data["title"]
    if not call.data.get("confirm"):
        raise HomeAssistantError(
            "You must set confirm to true after reviewing the job properties."
        )

    job = await store.async_get_job(title)
    if job is None:
        raise HomeAssistantError(f"No saved job with title '{title}'")

    # Check laser module matches
    if job.laser_module is not None:
        current = _current_laser_module(entry)
        if current is not None and current != job.laser_module:
            raise HomeAssistantError(
                f"Laser module mismatch: job was created with "
                f"'{job.laser_module}', but '{current}' is currently "
                f"installed. Swap the module or save a new job."
            )

    # Upload + start
    task_id = str(uuid.uuid4())
    try:
        await client.upload_job(job.gcode, task_id)
    except XToolS1ConnectionError as err:
        raise HomeAssistantError(f"Failed to upload gcode to laser: {err}") from err

    try:  # pragma: no branch
        await client.connect()
        await client.start_job_sequence()
    except XToolS1ConnectionError as err:
        raise HomeAssistantError(f"Start sequence failed: {err}") from err


async def async_delete_job(call: ServiceCall) -> None:
    """Delete a saved job by title."""
    store = _get_store(call.hass)
    title = call.data["title"]
    if not await store.async_delete_job(title):
        raise HomeAssistantError(f"No saved job with title '{title}'")


async def async_list_jobs(call: ServiceCall) -> ServiceResponse:
    """Return all saved jobs with metadata (no gcode body)."""
    store = _get_store(call.hass)
    jobs = await store.async_list_jobs()
    return {"jobs": jobs}


# --- schemas ---

SAVE_JOB_SCHEMA = vol.Schema(
    {
        vol.Required("title"): str,
        vol.Required("description"): str,
        vol.Required("material"): str,
        vol.Required("thickness_mm"): vol.Coerce(float),
    }
)

START_JOB_SCHEMA = vol.Schema(
    {
        vol.Required("title"): str,
        vol.Required("confirm"): bool,
    }
)

DELETE_JOB_SCHEMA = vol.Schema(
    {
        vol.Required("title"): str,
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register the xTool S1 services."""
    if hass.services.has_service(DOMAIN, "save_job"):
        return
    hass.services.async_register(
        DOMAIN, "save_job", async_save_job, schema=SAVE_JOB_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "start_job", async_start_job, schema=START_JOB_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "delete_job", async_delete_job, schema=DELETE_JOB_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "list_jobs",
        async_list_jobs,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
