"""Last plate seen per camera."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback, split_entity_id
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import NakordoniConfigEntry
from .api import RecognitionResult
from .const import SIGNAL_RESULT


async def async_setup_entry(
    hass: HomeAssistant, entry: NakordoniConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(LastPlateSensor(entry, cam) for cam in entry.runtime_data.cameras)


class LastPlateSensor(SensorEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:card-text-outline"

    def __init__(self, entry: NakordoniConfigEntry, camera: str) -> None:
        self._entry = entry
        self._camera = camera
        self._attr_name = f"nakordoni last plate {split_entity_id(camera)[1]}"
        self._attr_unique_id = f"{entry.entry_id}_{camera}_last_plate"
        self._attr_extra_state_attributes = {"camera_entity": camera}

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None and last.state not in ("unknown", "unavailable"):
            self._attr_native_value = last.state
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_RESULT.format(self._entry.entry_id), self._on_result)
        )

    @callback
    def _on_result(self, source: str, result: RecognitionResult) -> None:
        if source != self._camera or not result.results:
            return
        best = max(result.results, key=lambda r: r.get("score") or 0)
        self._attr_native_value = best.get("plate")
        self._attr_extra_state_attributes = {
            "camera_entity": self._camera,
            "confidence": best.get("score"),
            "region": best.get("region"),
            "box": best.get("box"),
            "candidates": best.get("candidates"),
        }
        self.async_write_ha_state()
