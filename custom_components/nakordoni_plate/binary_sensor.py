"""binary_sensor per watched plate: on for WATCHED_ON_SECONDS after a match.

Strict mode (default for new installs): on only after a strict match (see
gate.py). Loose mode (old behaviour, notify only, not for gates): any
reading or candidate counts.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import NakordoniConfigEntry
from .api import RecognitionResult, normalize_plate
from .const import SIGNAL_RESULT, SIGNAL_STRICT, WATCHED_ON_SECONDS
from .gate import StrictMatch


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
        self._strict = entry.runtime_data.is_strict(plate)
        self._attr_extra_state_attributes = {"plate": plate, "mode": "strict" if self._strict else "loose"}
        self._off = None

    async def async_added_to_hass(self) -> None:
        if self._strict:
            self.async_on_remove(
                async_dispatcher_connect(self.hass, SIGNAL_STRICT.format(self._entry.entry_id), self._on_strict)
            )
        else:
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
    def _on_strict(self, match: StrictMatch) -> None:
        if match.plate == self._plate:
            self._set_on(match.camera, match.score)

    @callback
    def _set_on(self, source: str, score) -> None:
        self._cancel()
        self._attr_is_on = True
        self._attr_extra_state_attributes = {
            "plate": self._plate,
            "mode": "strict" if self._strict else "loose",
            "camera_entity": source,
            "confidence": score,
            "last_seen": dt_util.now().isoformat(),
        }
        self.async_write_ha_state()
        self._off = async_call_later(self.hass, WATCHED_ON_SECONDS, self._turn_off)

    @callback
    def _on_result(self, source: str, result: RecognitionResult) -> None:
        for r in result.results:
            cands = [r.get("plate")] + [c.get("plate") for c in (r.get("candidates") or []) if isinstance(c, dict)]
            if self._plate in {normalize_plate(c) for c in cands}:
                self._set_on(source, r.get("score"))
                return

    @callback
    def _turn_off(self, _now) -> None:
        self._off = None
        self._attr_is_on = False
        self.async_write_ha_state()
