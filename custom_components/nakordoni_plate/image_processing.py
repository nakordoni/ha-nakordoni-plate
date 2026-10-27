"""image_processing entity per camera (shape modelled on platerecognizer)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.image_processing import ImageProcessingEntity
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NakordoniConfigEntry, async_recognize_and_publish
from .const import DOMAIN

# No polling here: auto-scan (scanner.py) or image_processing.scan drives it.
SCAN_INTERVAL = timedelta(days=3650)


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities: AddEntitiesCallback, discovery_info=None
) -> None:
    """Loaded by discovery from the config entry (core has no entry support here)."""
    if not discovery_info:
        return
    entry = hass.config_entries.async_get_entry(discovery_info["entry_id"])
    if entry is None or not hasattr(entry, "runtime_data"):
        return
    ents = [NakordoniPlateEntity(entry, cam) for cam in entry.runtime_data.cameras]
    entry.runtime_data.ip_entities.extend(ents)
    async_add_entities(ents)


class NakordoniPlateEntity(ImageProcessingEntity):
    """State = number of plates in the last processed image."""

    _attr_should_poll = False
    _attr_icon = "mdi:car"

    def __init__(self, entry: NakordoniConfigEntry, camera_entity: str) -> None:
        self._entry = entry
        self._camera = camera_entity
        obj = split_entity_id(camera_entity)[1]
        self._attr_name = f"nakordoni plate {obj}"
        self._attr_unique_id = f"{entry.entry_id}_{camera_entity}_ip"
        self._results: list[dict[str, Any]] = []
        self._processing_time: float | None = None
        self._message: str | None = None
        self._last_detection: str | None = None
        self._plates: list[str] = []

    @property
    def camera_entity(self) -> str:
        return self._camera

    @property
    def state(self) -> int:
        return len(self._results)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "vehicles": [
                {
                    "plate": r.get("plate"),
                    "confidence": r.get("score"),
                    "region_code": (r.get("region") or {}).get("code") if isinstance(r.get("region"), dict) else None,
                    "vehicle_type": (r.get("vehicle") or {}).get("type") if isinstance(r.get("vehicle"), dict) else None,
                }
                for r in self._results
            ],
            "plates": self._plates,
            "results": self._results,
            "processing_time": self._processing_time,
            "message": self._message,
            "last_detection": self._last_detection,
            "camera_entity": self._camera,
        }

    async def async_process_image(self, image: bytes) -> None:
        result = await async_recognize_and_publish(
            self.hass, self._entry, image=image, source=self._camera
        )
        self._results = result.results
        self._plates = [r.get("plate") for r in result.results if r.get("plate")]
        self._processing_time = result.processing_time
        self._message = result.message
        if result.results:
            self._last_detection = dt_util.now().isoformat()
