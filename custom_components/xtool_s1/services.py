"""Service handlers for the xTool S1 integration.

Exposes four services for job management:

* ``xtool_s1.save_job`` — download + persist with metadata
* ``xtool_s1.start_job`` — upload + WS start sequence (confirm required)
* ``xtool_s1.delete_job`` — remove a saved job
* ``xtool_s1.list_jobs`` — return all saved jobs

All device-targeting services accept an optional ``device_id``
parameter.  When only one xTool S1 is configured it is selected
automatically; with multiple devices ``device_id`` is required.
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
from homeassistant.helpers import device_registry as dr
import voluptuous as vol

from .api import XToolS1ConnectionError
from .const import DOMAIN, resolve_tool_name
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
    if data is None:
        return None
    return resolve_tool_name(
        tool_power_w=data.tool_power_w,
        firmware_fingerprint=data.firmware_aux_1,
    )


def _resolve_entry(
    hass: HomeAssistant, device_id: str | None = None
) -> XToolS1ConfigEntry:
    """Resolve the config entry for a service call.

    * *device_id* given → look up that device in the registry.
    * *device_id* omitted, single entry → auto-select.
    * *device_id* omitted, multiple entries → raise.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("No xTool S1 device configured")

    if device_id is not None:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Unknown device: {device_id}")
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None and entry.domain == DOMAIN:
                return entry  # type: ignore[return-value]
        raise HomeAssistantError(f"Device {device_id} is not an xTool S1")

    if len(entries) == 1:
        return entries[0]  # type: ignore[return-value]

    raise HomeAssistantError(
        f"Multiple xTool S1 devices configured ({len(entries)}). "
        "Please specify a device_id."
    )


def _get_serial(entry: XToolS1ConfigEntry) -> str:
    """Return the serial number for a config entry (always available)."""
    return entry.unique_id or entry.entry_id


def _get_store(hass: HomeAssistant) -> XToolS1JobStore:
    key = f"{DOMAIN}_job_store"
    if key not in hass.data:
        hass.data[key] = XToolS1JobStore(hass)
    store: XToolS1JobStore = hass.data[key]
    return store


# --- service handlers ---


async def async_save_job(call: ServiceCall) -> None:
    """Download current gcode from laser and persist with metadata."""
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get("device_id"))
    client = entry.runtime_data.client
    store = _get_store(hass)

    try:
        gcode = await client.download_job()
    except XToolS1ConnectionError as err:
        raise HomeAssistantError(f"Failed to download gcode from laser: {err}") from err

    job = SavedJob(
        title=call.data["title"],
        description=call.data["description"],
        material=call.data["material"],
        thickness_mm=float(call.data["thickness_mm"]),
        gcode=gcode,
        saved_at=XToolS1JobStore.now_iso(),
        serial_number=_get_serial(entry),
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
    entry = _resolve_entry(hass, call.data.get("device_id"))
    client = entry.runtime_data.client
    store = _get_store(hass)
    serial = _get_serial(entry)

    title = call.data["title"]
    if not call.data.get("confirm"):
        raise HomeAssistantError(
            "You must set confirm to true after reviewing the job properties."
        )

    job = await store.async_get_job(title, serial)
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
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get("device_id"))
    serial = _get_serial(entry)
    store = _get_store(hass)
    title = call.data["title"]
    if not await store.async_delete_job(title, serial):
        raise HomeAssistantError(f"No saved job with title '{title}'")


async def async_list_jobs(call: ServiceCall) -> ServiceResponse:
    """Return all saved jobs with metadata (no gcode body)."""
    hass = call.hass
    store = _get_store(hass)
    device_id = call.data.get("device_id")
    serial: str | None = None
    if device_id is not None:
        entry = _resolve_entry(hass, device_id)
        serial = _get_serial(entry)
    jobs = await store.async_list_jobs(serial_number=serial)
    return {"jobs": jobs}  # type: ignore[dict-item]


# --- schemas ---

SAVE_JOB_SCHEMA = vol.Schema(
    {
        vol.Required("title"): str,
        vol.Required("description"): str,
        vol.Required("material"): str,
        vol.Required("thickness_mm"): vol.Coerce(float),
        vol.Optional("device_id"): str,
    }
)

START_JOB_SCHEMA = vol.Schema(
    {
        vol.Required("title"): str,
        vol.Required("confirm"): bool,
        vol.Optional("device_id"): str,
    }
)

DELETE_JOB_SCHEMA = vol.Schema(
    {
        vol.Required("title"): str,
        vol.Optional("device_id"): str,
    }
)

LIST_JOBS_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): str,
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
        schema=LIST_JOBS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
