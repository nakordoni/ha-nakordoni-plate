"""Strict watched-plate matching and the built-in gate action (v0.4.0).

Strict mode: only the TOP reading counts (never ``candidates``), the plate
must match after normalisation, the score must reach the strict minimum and
N consecutive matching reads on one camera must fall inside the window.
A gate only opens on such a strict match.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .api import normalize_plate
from .const import (
    CONF_GATE_CAMERAS,
    CONF_GATE_COOLDOWN,
    CONF_GATE_END,
    CONF_GATE_ENTITY,
    CONF_GATE_ID,
    CONF_GATE_NAME,
    CONF_GATE_PLATES,
    CONF_GATE_START,
    DEFAULT_GATE_COOLDOWN,
    DOMAIN,
    EVENT_GATE_OPENED,
    GATE_ACTIONS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class StrictMatch:
    plate: str
    camera: str
    score: float


def top_reading(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The single highest-score reading. Candidates are never looked at."""
    best = None
    for r in results:
        if best is None or float(r.get("score") or 0) > float(best.get("score") or 0):
            best = r
    return best


class StrictMatcher:
    """Per camera: the plate of the current streak + times of its reads."""

    def __init__(self, min_score: float, reads: int, window: float) -> None:
        self.min_score = min_score
        self.reads = max(1, int(reads))
        self.window = float(window)
        self._streak: dict[str, tuple[str, list[float]]] = {}

    def current(self, camera: str) -> str | None:
        prev = self._streak.get(camera)
        return prev[0] if prev else None

    def feed(self, camera: str, results: list[dict[str, Any]]) -> tuple[StrictMatch | None, bool]:
        """Return (confirmed match or None, pending: a streak has started but is short)."""
        now = dt_util.utcnow().timestamp()
        top = top_reading(results)
        score = float((top or {}).get("score") or 0)
        plate = normalize_plate(str((top or {}).get("plate") or ""))
        if top is None or not plate or score < self.min_score:
            self._streak.pop(camera, None)  # a non-matching read breaks the streak
            return None, False
        prev = self._streak.get(camera)
        times = prev[1] if prev and prev[0] == plate else []
        times = [t for t in times if now - t <= self.window] + [now]
        self._streak[camera] = (plate, times[-self.reads:])
        if len(times) >= self.reads:
            return StrictMatch(plate, camera, score), False
        return None, True


def _parse_time(value: Any) -> time | None:
    if not value:
        return None
    if isinstance(value, time):
        return value
    parts = [int(p) for p in str(value).split(":")]
    return time(*parts[:3])


def in_schedule(gate: dict[str, Any], now: datetime) -> bool:
    """No schedule = always. start > end = window over midnight."""
    start, end = _parse_time(gate.get(CONF_GATE_START)), _parse_time(gate.get(CONF_GATE_END))
    if start is None and end is None:
        return True
    t = now.time().replace(tzinfo=None)
    start = start or time(0, 0)
    end = end or time(23, 59, 59)
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def gate_cameras(gate: dict[str, Any], all_cameras: list[str]) -> list[str]:
    return list(gate.get(CONF_GATE_CAMERAS) or all_cameras)


class GateController:
    """Opens gates on strict matches. One per config entry."""

    def __init__(self, hass: HomeAssistant, gates: list[dict[str, Any]]) -> None:
        self.hass = hass
        self.gates = gates
        self.enabled: dict[str, bool] = {g[CONF_GATE_ID]: True for g in gates}
        self._last: dict[str, float] = {}

    def wants(self, plate: str, camera: str) -> bool:
        return any(
            plate in {normalize_plate(p) for p in g.get(CONF_GATE_PLATES) or []}
            and (not g.get(CONF_GATE_CAMERAS) or camera in g[CONF_GATE_CAMERAS])
            for g in self.gates
        )

    async def async_handle(self, match: StrictMatch) -> list[str]:
        """Open every gate this match is allowed to open. Returns the gate ids opened."""
        opened: list[str] = []
        now = dt_util.utcnow().timestamp()
        for g in self.gates:
            gid = g[CONF_GATE_ID]
            if match.plate not in {normalize_plate(p) for p in g.get(CONF_GATE_PLATES) or []}:
                continue
            if g.get(CONF_GATE_CAMERAS) and match.camera not in g[CONF_GATE_CAMERAS]:
                continue
            if not self.enabled.get(gid, True):
                continue
            if not in_schedule(g, dt_util.now()):
                continue
            last = self._last.get(gid)
            cooldown = float(g.get(CONF_GATE_COOLDOWN, DEFAULT_GATE_COOLDOWN))
            if last is not None and now - last < cooldown:
                continue
            entity = g[CONF_GATE_ENTITY]
            domain = entity.split(".", 1)[0]
            service = GATE_ACTIONS.get(domain)
            if service is None:
                _LOGGER.warning("Gate %s: unsupported entity %s", gid, entity)
                continue
            self._last[gid] = now
            try:
                await self.hass.services.async_call(
                    domain, service, {"entity_id": entity}, blocking=True
                )
            except HomeAssistantError as err:
                _LOGGER.error("Gate %s: %s.%s on %s failed: %s", gid, domain, service, entity, err)
                continue
            data = {"plate": match.plate, "camera": match.camera, "gate": entity, "score": match.score}
            self.hass.bus.async_fire(EVENT_GATE_OPENED, data)
            self.hass.bus.async_fire(
                "logbook_entry",
                {
                    "name": g.get(CONF_GATE_NAME) or entity,
                    "message": f"opened for plate {match.plate} (camera {match.camera}, score {match.score:.2f})",
                    "entity_id": entity,
                    "domain": DOMAIN,
                },
            )
            opened.append(gid)
        return opened
