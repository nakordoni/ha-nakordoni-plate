"""Auto-scan: run recognition per camera on motion or every N seconds.

Each scan is one API call. A cooldown limits scans per camera, and the
event dedupe (see ``Deduper``) stops the same plate firing twice in a row.
"""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.camera import async_get_image
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CAMERA_SCAN,
    CONF_COOLDOWN,
    CONF_MOTION_ENTITY,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_MODE,
    DEFAULT_AUTO_INTERVAL,
    DEFAULT_COOLDOWN,
    DEFAULT_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MOTION_DEVICE_CLASSES,
    SCAN_INTERVAL,
    SCAN_MOTION,
    SCAN_OFF,
)

if TYPE_CHECKING:
    from . import NakordoniConfigEntry

_LOGGER = logging.getLogger(__name__)
# Timers drift by a fraction of a second; do not let "every 10 s" + "cooldown 10 s" skip every other scan.
_COOLDOWN_SLACK = 0.5


class Deduper:
    """Remember (camera, plate) -> last fire time; drop repeats inside the window."""

    def __init__(self, window: float) -> None:
        self.window = window
        self._seen: dict[tuple[str, str], float] = {}

    def is_new(self, source: str, plate: str) -> bool:
        now = dt_util.utcnow().timestamp()
        key = (source, plate)
        last = self._seen.get(key)
        self._seen[key] = now
        if len(self._seen) > 512:
            self._seen = {k: t for k, t in self._seen.items() if now - t < self.window}
        return last is None or now - last >= self.window


def find_motion_entity(hass: HomeAssistant, camera: str) -> str | None:
    """A motion/occupancy/presence binary_sensor on the camera's device, if any."""
    ent_reg = er.async_get(hass)
    cam = ent_reg.async_get(camera)
    if cam is None or cam.device_id is None:
        return None
    best: str | None = None
    for ent in er.async_entries_for_device(ent_reg, cam.device_id):
        if ent.domain != "binary_sensor" or ent.disabled:
            continue
        dc = ent.device_class or ent.original_device_class
        if dc == "motion":
            return ent.entity_id
        if dc in MOTION_DEVICE_CLASSES and best is None:
            best = ent.entity_id
    return best


def default_camera_scan(hass: HomeAssistant, camera: str) -> dict[str, Any]:
    """New camera: on motion when the device has a motion sensor, else every 30 s."""
    motion = find_motion_entity(hass, camera)
    if motion:
        return {CONF_SCAN_MODE: SCAN_MOTION, CONF_MOTION_ENTITY: motion, CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL}
    return {CONF_SCAN_MODE: SCAN_INTERVAL, CONF_MOTION_ENTITY: None, CONF_SCAN_INTERVAL: DEFAULT_AUTO_INTERVAL}


def camera_scan_config(hass: HomeAssistant, options: dict[str, Any], camera: str) -> dict[str, Any]:
    saved = (options.get(CONF_CAMERA_SCAN) or {}).get(camera)
    return dict(saved) if saved else default_camera_scan(hass, camera)


class CameraScanner:
    """Owns the listeners for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: NakordoniConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.cooldown = float(entry.options.get(CONF_COOLDOWN, DEFAULT_COOLDOWN))
        self._last_scan: dict[str, float] = {}
        self._busy: set[str] = set()

    @callback
    def async_start(self) -> list[Any]:
        unsubs: list[Any] = []
        for cam in self.entry.runtime_data.cameras:
            cfg = camera_scan_config(self.hass, dict(self.entry.options), cam)
            mode = cfg.get(CONF_SCAN_MODE, SCAN_OFF)
            if mode == SCAN_MOTION and cfg.get(CONF_MOTION_ENTITY):
                unsubs.append(
                    async_track_state_change_event(
                        self.hass, [cfg[CONF_MOTION_ENTITY]], self._motion_cb(cam)
                    )
                )
            elif mode == SCAN_INTERVAL:
                secs = max(MIN_SCAN_INTERVAL, int(cfg.get(CONF_SCAN_INTERVAL) or DEFAULT_AUTO_INTERVAL))
                unsubs.append(
                    async_track_time_interval(
                        self.hass, self._interval_cb(cam), timedelta(seconds=secs),
                        name=f"nakordoni_plate scan {cam}",
                    )
                )
        return unsubs

    def _motion_cb(self, cam: str):
        @callback
        def _cb(event: Event[EventStateChangedData]) -> None:
            old, new = event.data["old_state"], event.data["new_state"]
            if new is None or new.state != STATE_ON:
                return
            if old is not None and old.state != STATE_OFF:
                return  # only a real off -> on edge (not unavailable -> on)
            self.entry.async_create_background_task(
                self.hass, self.async_scan(cam), f"nakordoni_plate motion scan {cam}"
            )

        return _cb

    def _interval_cb(self, cam: str):
        async def _cb(_now) -> None:
            await self.async_scan(cam)

        return _cb

    async def async_scan(self, cam: str, force: bool = False) -> bool:
        """Scan one camera now. Returns True when an API call was made.

        ``force`` skips the cooldown (strict-mode follow-up read for a gate).
        """
        state = self.hass.states.get(cam)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        now = dt_util.utcnow().timestamp()
        last = self._last_scan.get(cam)
        if cam in self._busy or (
            not force and last is not None and now - last < self.cooldown - _COOLDOWN_SLACK
        ):
            return False
        self._last_scan[cam] = now
        self._busy.add(cam)
        try:
            ent = next((e for e in self.entry.runtime_data.ip_entities if e.camera_entity == cam), None)
            if ent is not None and ent.hass is not None:
                img = await async_get_image(self.hass, cam)
                await ent.async_process_image(img.content)
                ent.async_write_ha_state()
            else:
                from . import async_recognize_and_publish

                img = await async_get_image(self.hass, cam)
                await async_recognize_and_publish(self.hass, self.entry, image=img.content, source=cam)
        except HomeAssistantError as err:
            _LOGGER.warning("Auto-scan of %s failed: %s", cam, err)
        finally:
            self._busy.discard(cam)
        return True
