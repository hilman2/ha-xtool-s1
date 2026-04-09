"""Service handlers for the xTool S1 integration.

Exposes three services:

* ``xtool_s1.save_job`` — download the current gcode from the laser
  and persist it with user-supplied metadata.  The laser module and
  power percentage are extracted automatically from the gcode.
* ``xtool_s1.start_job`` — upload a saved job and run the WebSocket
  start sequence.  Requires material + thickness confirmation, a risk
  acknowledgement, and validates that the currently installed laser
  module matches the one the job was created with.
* ``xtool_s1.list_jobs`` — return all saved jobs as a service response.
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

# Regex to extract laser power info from gcode comments.
_POWER_FACTOR_RE = re.compile(r'"powerFactor"\s*:\s*([\d.]+)')


def _extract_gcode_power_percent(gcode: str) -> float | None:
    """Extract the laser power percentage from a gcode body."""
    match = _POWER_FACTOR_RE.search(gcode)
    if match:
        try:
            return round(float(match.group(1)) * 100, 2)
        except ValueError:  # pragma: no cover — regex constrains to digits
            pass
    return None


def _current_laser_module(entry: XToolS1ConfigEntry) -> str | None:
    """Return the human-readable name of the currently installed tool."""
    data = entry.runtime_data.coordinator.data
    if data is None or data.firmware_aux_1 is None:
        return None
    return TOOL_FIRMWARE_NAMES.get(data.firmware_aux_1, "Unknown")


def _get_entry(hass: HomeAssistant) -> XToolS1ConfigEntry:
    """Get the first config entry for the domain."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No xTool S1 device configured")
    return entries[0]  # type: ignore[return-value]


def _get_store(hass: HomeAssistant) -> XToolS1JobStore:
    """Get or create the job store singleton."""
    key = f"{DOMAIN}_job_store"
    if key not in hass.data:
        hass.data[key] = XToolS1JobStore(hass)
    return hass.data[key]


async def async_save_job(call: ServiceCall) -> None:
    """Handle the save_job service call."""
    hass = call.hass
    entry = _get_entry(hass)
    client = entry.runtime_data.client
    store = _get_store(hass)

    title = call.data["title"]
    description = call.data["description"]
    material = call.data["material"]
    thickness_mm = float(call.data["thickness_mm"])

    try:
        gcode = await client.download_job()
    except XToolS1ConnectionError as err:
        raise HomeAssistantError(f"Failed to download gcode from laser: {err}") from err

    state = entry.runtime_data.coordinator.data
    serial_number = state.serial_number if state else None
    laser_module = _current_laser_module(entry)
    power_percent = _extract_gcode_power_percent(gcode)

    job = SavedJob(
        title=title,
        description=description,
        material=material,
        thickness_mm=thickness_mm,
        gcode=gcode,
        saved_at=XToolS1JobStore.now_iso(),
        serial_number=serial_number,
        laser_module=laser_module,
        power_percent=power_percent,
    )
    await store.async_save_job(job)


async def async_start_job(call: ServiceCall) -> None:
    """Handle the start_job service call."""
    hass = call.hass
    entry = _get_entry(hass)
    client = entry.runtime_data.client
    store = _get_store(hass)

    title = call.data["title"]
    confirm_material = call.data["confirm_material"]
    confirm_thickness = float(call.data["confirm_thickness_mm"])
    confirm_risk = call.data["confirm_risk"]

    if not confirm_risk:
        raise HomeAssistantError(
            "You must set confirm_risk to true to acknowledge that you have "
            "verified material, thickness and laser settings, and accept "
            "full responsibility for the operation."
        )

    job = await store.async_get_job(title)
    if job is None:
        raise HomeAssistantError(f"No saved job with title '{title}'")

    # --- safety checks ---

    # Material must match (case-insensitive)
    if job.material.lower().strip() != confirm_material.lower().strip():
        raise HomeAssistantError(
            f"Material mismatch: job expects '{job.material}', "
            f"got '{confirm_material}'"
        )

    # Thickness must match (within 0.05 mm tolerance)
    if abs(job.thickness_mm - confirm_thickness) > 0.05:
        raise HomeAssistantError(
            f"Thickness mismatch: job expects {job.thickness_mm} mm, "
            f"got {confirm_thickness} mm"
        )

    # Laser module must match the one the job was saved with
    if job.laser_module is not None:
        current = _current_laser_module(entry)
        if current is not None and current != job.laser_module:
            raise HomeAssistantError(
                f"Laser module mismatch: job was created with "
                f"'{job.laser_module}', but '{current}' is currently "
                f"installed. Swap the module or save a new job."
            )

    # --- upload + start ---

    task_id = str(uuid.uuid4())
    try:
        await client.upload_job(job.gcode, task_id)
    except XToolS1ConnectionError as err:
        raise HomeAssistantError(f"Failed to upload gcode to laser: {err}") from err

    try:  # pragma: no branch
        await client.connect()  # idempotent if already connected
        await client.start_job_sequence()
    except XToolS1ConnectionError as err:
        raise HomeAssistantError(f"Start sequence failed: {err}") from err


async def async_list_jobs(call: ServiceCall) -> ServiceResponse:
    """Handle the list_jobs service call."""
    store = _get_store(call.hass)
    jobs = await store.async_list_jobs()
    return {"jobs": jobs}


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
        vol.Required("confirm_material"): str,
        vol.Required("confirm_thickness_mm"): vol.Coerce(float),
        vol.Required("confirm_risk"): bool,
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    """Register the xTool S1 services."""
    if hass.services.has_service(DOMAIN, "save_job"):
        return  # already registered (multiple config entries)
    hass.services.async_register(
        DOMAIN,
        "save_job",
        async_save_job,
        schema=SAVE_JOB_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "start_job",
        async_start_job,
        schema=START_JOB_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "list_jobs",
        async_list_jobs,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
