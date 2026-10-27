"""Optional Frigate bridge: MQTT frigate/events type=end -> recognize snapshot -> sub_label."""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import aiohttp

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FRIGATE_LABELS,
    CONF_FRIGATE_TOPIC,
    CONF_FRIGATE_URL,
    DEFAULT_FRIGATE_LABELS,
    DEFAULT_FRIGATE_TOPIC,
    DEFAULT_FRIGATE_URL,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_frigate(hass: HomeAssistant, entry) -> None:
    from . import async_recognize_and_publish

    if not await mqtt.async_wait_for_mqtt_client(hass):
        _LOGGER.warning("Frigate bridge enabled but MQTT is not available")
        return
    o = entry.options
    base = (o.get(CONF_FRIGATE_URL) or DEFAULT_FRIGATE_URL).rstrip("/")
    labels = {s.strip() for s in (o.get(CONF_FRIGATE_LABELS) or DEFAULT_FRIGATE_LABELS).split(",") if s.strip()}
    session = async_get_clientsession(hass)
    seen: set[str] = set()

    async def handle(event_id: str, camera: str) -> None:
        async def recognize(image: bytes):
            return await async_recognize_and_publish(
                hass, entry, image=image, source=f"camera.{camera}"
            )

        try:
            await async_process_frigate_event(session, base, event_id, recognize)
        except (aiohttp.ClientError, HomeAssistantError, TimeoutError) as err:
            _LOGGER.warning("Frigate bridge %s: %s", event_id, err)

    @callback
    def on_message(msg) -> None:
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        if payload.get("type") != "end":
            return
        after = payload.get("after") or {}
        eid = after.get("id")
        if not eid or eid in seen or after.get("label") not in labels or not after.get("has_snapshot", True):
            return
        seen.add(eid)
        if len(seen) > 500:
            seen.clear()
        entry.async_create_background_task(hass, handle(eid, after.get("camera", "frigate")), f"nakordoni_frigate_{eid}")

    unsub = await mqtt.async_subscribe(hass, o.get(CONF_FRIGATE_TOPIC) or DEFAULT_FRIGATE_TOPIC, on_message)
    entry.runtime_data.unsubs.append(unsub)


async def async_process_frigate_event(
    session: aiohttp.ClientSession,
    base: str,
    event_id: str,
    recognize: Callable[[bytes], Awaitable[Any]],
) -> dict | None:
    """Snapshot -> recognize -> sub_label. Returns the posted body (or None).

    Checked against Frigate 0.15.1: GET /api/events/<id>/snapshot.jpg and
    POST /api/events/<id>/sub_label {"subLabel", "subLabelScore"} (score 0-1).
    """
    async with session.get(
        f"{base}/api/events/{event_id}/snapshot.jpg",
        params={"crop": "0", "quality": "95", "bbox": "0", "timestamp": "0"},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        if resp.status != 200:
            _LOGGER.debug("Frigate snapshot %s: HTTP %s", event_id, resp.status)
            return None
        image = await resp.read()
    result = await recognize(image)
    if not result.results:
        return None
    best = max(result.results, key=lambda r: r.get("score") or 0)
    body: dict[str, Any] = {"subLabel": str(best.get("plate"))[:100]}
    if best.get("score") is not None:
        body["subLabelScore"] = round(min(max(float(best["score"]), 0.0), 1.0), 2)
    async with session.post(
        f"{base}/api/events/{event_id}/sub_label", json=body,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        if resp.status >= 400:
            _LOGGER.warning("Frigate sub_label %s: HTTP %s %s", event_id, resp.status, await resp.text())
            return None
    return body
