"""Persistent storage for saved xTool S1 jobs.

Jobs are stored as a JSON dict keyed by ``serial/title``, each
containing the gcode body and user-supplied metadata (material,
thickness, description).  The composite key ensures that two devices
can each have a job with the same title without colliding.

Legacy stores (pre-multi-device) used plain *title* keys.  These are
migrated transparently on first load.

The HA Store helper writes to ``config/.storage/xtool_s1_jobs``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORAGE_KEY = "xtool_s1_jobs"
STORAGE_VERSION = 1


def _make_key(serial: str, title: str) -> str:
    """Build the composite storage key ``serial/title``."""
    return f"{serial}/{title}"


@dataclass(frozen=True)
class SavedJob:
    """A saved gcode job with metadata."""

    title: str
    description: str
    material: str
    thickness_mm: float
    gcode: str
    saved_at: str  # ISO 8601
    serial_number: str | None = None
    laser_module: str | None = None  # e.g. "Diode 40 W"
    power_percent: float | None = None  # extracted from gcode
    speed_mm_per_s: float | None = None  # max feed from gcode
    laser_mode: str | None = None  # "cut" or "frame"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SavedJob:
        return cls(
            title=data["title"],
            description=data["description"],
            material=data["material"],
            thickness_mm=float(data["thickness_mm"]),
            gcode=data["gcode"],
            saved_at=data["saved_at"],
            serial_number=data.get("serial_number"),
            laser_module=data.get("laser_module"),
            power_percent=data.get("power_percent"),
            speed_mm_per_s=data.get("speed_mm_per_s"),
            laser_mode=data.get("laser_mode"),
        )


class XToolS1JobStore:
    """Thin wrapper around HA's Store for saved jobs."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] | None = None

    async def async_load(self) -> dict[str, SavedJob]:
        if self._data is None:
            raw = await self._store.async_load()
            self._data = raw if isinstance(raw, dict) else {}
            await self._migrate_legacy_keys()
        return {k: SavedJob.from_dict(v) for k, v in self._data.items()}

    async def _migrate_legacy_keys(self) -> None:
        """Migrate old flat title keys to composite ``serial/title`` keys."""
        assert self._data is not None
        to_add: dict[str, dict[str, Any]] = {}
        to_remove: list[str] = []
        for key, value in self._data.items():
            if "/" not in key:
                serial = value.get("serial_number") or "unknown"
                to_add[_make_key(serial, key)] = value
                to_remove.append(key)
        if not to_remove:
            return
        for key in to_remove:
            del self._data[key]
        self._data.update(to_add)
        await self._store.async_save(self._data)

    async def async_save_job(self, job: SavedJob) -> None:
        await self.async_load()
        assert self._data is not None
        key = _make_key(job.serial_number or "unknown", job.title)
        self._data[key] = job.to_dict()
        await self._store.async_save(self._data)

    async def async_get_job(self, title: str, serial_number: str) -> SavedJob | None:
        """Look up a saved job by title and device serial."""
        jobs = await self.async_load()
        return jobs.get(_make_key(serial_number, title))

    async def async_list_jobs(
        self, serial_number: str | None = None
    ) -> list[dict[str, Any]]:
        """Return all saved jobs, optionally filtered by device serial."""
        jobs = await self.async_load()
        return [
            {
                "title": j.title,
                "description": j.description,
                "material": j.material,
                "thickness_mm": j.thickness_mm,
                "serial_number": j.serial_number,
                "laser_module": j.laser_module,
                "power_percent": j.power_percent,
                "speed_mm_per_s": j.speed_mm_per_s,
                "laser_mode": j.laser_mode,
                "saved_at": j.saved_at,
            }
            for j in jobs.values()
            if serial_number is None or j.serial_number == serial_number
        ]

    async def async_delete_job(self, title: str, serial_number: str) -> bool:
        """Delete a saved job by title and device serial."""
        await self.async_load()
        assert self._data is not None
        key = _make_key(serial_number, title)
        if key not in self._data:
            return False
        del self._data[key]
        await self._store.async_save(self._data)
        return True

    @staticmethod
    def now_iso() -> str:
        return datetime.now(UTC).isoformat()
