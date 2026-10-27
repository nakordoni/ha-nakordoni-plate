"""binary_sensor per watched plate: on for WATCHED_ON_SECONDS after a match."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import NakordoniConfigEntry
from .api import RecognitionResult, normalize_plate
from .const import SIGNAL_RESULT, WATCHED_ON_SECONDS


async def async_setup_entry(
    hass: HomeAssistant, entry: NakordoniConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(WatchedPlateSensor(entry, p) for p in entry.runtime_data.watched if p)


class WatchedPlateSensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:car-search"
    _attr_is_on = False

    def __init__(self, entry: NakordoniConfigEntry, plate: str) -> None:
        self._entry = entry
        self._plate = plate
        self._attr_name = f"nakordoni watched {plate}"
        self._attr_unique_id = f"{entry.entry_id}_watched_{plate}"
        self._attr_extra_state_attributes = {"plate": plate}
        self._off = None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_RESULT.format(self._entry.entry_id), self._on_result)
        )
        self.async_on_remove(self._cancel)

    @callback
    def _cancel(self) -> None:
        if self._off:
            self._off()
            self._off = None

    @callback
    def _on_result(self, source: str, result: RecognitionResult) -> None:
        for r in result.results:
            cands = [r.get("plate")] + [c.get("plate") for c in (r.get("candidates") or []) if isinstance(c, dict)]
            if self._plate in {normalize_plate(c) for c in cands}:
                self._cancel()
                self._attr_is_on = True
                self._attr_extra_state_attributes = {
                    "plate": self._plate,
                    "camera_entity": source,
                    "confidence": r.get("score"),
                    "last_seen": dt_util.now().isoformat(),
                }
                self.async_write_ha_state()
                self._off = async_call_later(self.hass, WATCHED_ON_SECONDS, self._turn_off)
                return

    @callback
    def _turn_off(self, _now) -> None:
        self._off = None
        self._attr_is_on = False
        self.async_write_ha_state()
