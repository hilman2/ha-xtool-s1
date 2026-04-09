"""Button platform for the xTool S1 — job-control actions over HTTP.

All three buttons go through the HTTP ``POST /cmd`` gateway so they
keep working even when the XCS desktop app is hammering the
WebSocket. They are intentionally **HTTP-only entities** that
inherit from :class:`XToolS1HttpEntity` and stay available as long
as the device's HTTP gateway answers.

Status (verified against hilman2's S1 on 2026-04-09):

* **Stop** — `M108`. Verified live: the device acks with `M108 ok`
  and runs the shutdown state machine (S18 → S1 → S3, with M22
  sticky at S1 as the abnormal-finish marker).
* **Pause** — `M22 S1`. Verified from the same Wireshark capture:
  the device echoes ``M22 S1``, transitions ``M222`` to S15 and
  dims the fill light to ``M15 A1 S0``.
* **Resume** — best-effort. The S1 firmware does **not** accept a
  network resume request — the user must press the physical Start
  button on the device. The button is kept as a documentation /
  automation entity and sends ``M22 S2`` in case a future firmware
  starts honouring it; expect it to be a no-op until then.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import XToolS1Client, XToolS1ConnectionError
from .const import BUTTON_PAUSE, BUTTON_RESUME, BUTTON_STOP
from .coordinator import XToolS1ConfigEntry
from .entity import XToolS1HttpEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class XToolS1ButtonDescription(ButtonEntityDescription):
    """Describes an xTool S1 button."""

    press_fn: Callable[[XToolS1Client], Awaitable[None]]


BUTTON_DESCRIPTIONS: tuple[XToolS1ButtonDescription, ...] = (
    XToolS1ButtonDescription(
        key=BUTTON_STOP,
        translation_key=BUTTON_STOP,
        press_fn=lambda client: client.stop_job(),
    ),
    XToolS1ButtonDescription(
        key=BUTTON_PAUSE,
        translation_key=BUTTON_PAUSE,
        press_fn=lambda client: client.pause_job(),
    ),
    XToolS1ButtonDescription(
        key=BUTTON_RESUME,
        translation_key=BUTTON_RESUME,
        press_fn=lambda client: client.resume_job(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XToolS1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the xTool S1 control buttons from a config entry."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        XToolS1Button(coordinator, description) for description in BUTTON_DESCRIPTIONS
    )


class XToolS1Button(XToolS1HttpEntity, ButtonEntity):
    """A single HTTP-routed control button (stop/pause/resume)."""

    entity_description: XToolS1ButtonDescription

    def __init__(
        self,
        coordinator,
        description: XToolS1ButtonDescription,
    ) -> None:
        self._attr_translation_key = description.translation_key or description.key
        super().__init__(coordinator)
        self.entity_description = description

    async def async_press(self) -> None:
        """Send the button's M-code via the HTTP gateway."""
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except XToolS1ConnectionError as err:
            raise HomeAssistantError(
                f"Failed to send {self.entity_description.key} command: {err}"
            ) from err
