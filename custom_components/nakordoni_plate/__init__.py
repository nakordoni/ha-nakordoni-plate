"""Nakordoni DevAPI plate recognition for Home Assistant."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv, discovery
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import NakordoniAuthError, NakordoniError, NakordoniPlateClient, RecognitionResult, normalize_plate
from .const import (
    ATTR_CAMERA_ENTITY,
    ATTR_FILE_PATH,
    ATTR_IMAGE_URL,
    AUTH_API_KEY,
    BUNDLED_AUTH_DOMAIN,
    BUNDLED_CLIENT_ID,
    BUNDLED_CLIENT_SECRET,
    CONF_API_URL,
    CONF_AUTH_TYPE,
    CONF_CAMERAS,
    CONF_COOLDOWN,
    CONF_FAST_MODE,
    CONF_GATES,
    CONF_LOOSE_PLATES,
    CONF_STRICT_MIN_SCORE,
    CONF_STRICT_READS,
    CONF_STRICT_WINDOW,
    CONF_WATCHED_MODE,
    DEFAULT_STRICT_MIN_SCORE,
    DEFAULT_STRICT_READS,
    DEFAULT_STRICT_WINDOW,
    SIGNAL_STRICT,
    STRICT_FOLLOWUP_DELAY,
    WATCHED_LOOSE,
    WATCHED_STRICT,
    CONF_FRIGATE_ENABLED,
    CONF_MIN_SCORE,
    CONF_REGIONS,
    CONF_WATCHED_PLATES,
    DEFAULT_API_URL,
    DEFAULT_COOLDOWN,
    DEFAULT_MIN_SCORE,
    DOMAIN,
    EVENT_VEHICLE_DETECTED,
    MAX_IMAGE_BYTES,
    SERVICE_RECOGNIZE,
    SIGNAL_RESULT,
)
from .gate import GateController, StrictMatcher, gate_cameras
from .scanner import CameraScanner, Deduper

_LOGGER = logging.getLogger(__name__)
# image_processing has no config-entry support in core: loaded via discovery
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class NakordoniData:
    """Runtime data stored on the config entry."""

    client: NakordoniPlateClient
    regions: list[str]
    min_score: float
    watched: list[str]
    cameras: list[str]
    dedupe: Deduper = field(default_factory=lambda: Deduper(DEFAULT_COOLDOWN))
    scanner: CameraScanner | None = None
    unsubs: list[Any] = field(default_factory=list)
    ip_entities: list[Any] = field(default_factory=list)
    strict_plates: set[str] = field(default_factory=set)  # watched plates in strict mode
    matcher: StrictMatcher | None = None
    gates: GateController | None = None
    fast: dict[str, bool] = field(default_factory=dict)

    def is_strict(self, plate: str) -> bool:
        return plate in self.strict_plates


type NakordoniConfigEntry = ConfigEntry[NakordoniData]


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    _register_service(hass)
    await _async_import_bundled_credential(hass)
    return True


async def _async_import_bundled_credential(hass: HomeAssistant) -> None:
    """Ship the first-party public client so users need no own credentials."""
    from homeassistant.components.application_credentials import (
        ClientCredential,
        async_import_client_credential,
    )

    try:
        await async_import_client_credential(
            hass,
            DOMAIN,
            ClientCredential(BUNDLED_CLIENT_ID, BUNDLED_CLIENT_SECRET, "Nakordoni"),
            BUNDLED_AUTH_DOMAIN,
        )
    except Exception as err:  # noqa: BLE001 - credential import must never block setup
        _LOGGER.debug("Bundled credential import skipped: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: NakordoniConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    if entry.data.get(CONF_AUTH_TYPE) == AUTH_API_KEY:
        key = entry.data["api_key"]

        async def token() -> str:
            return key
    else:
        impl = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
        oauth = config_entry_oauth2_flow.OAuth2Session(hass, entry, impl)

        async def token() -> str:
            try:
                await oauth.async_ensure_token_valid()
            except aiohttp.ClientResponseError as err:
                if err.status in (400, 401, 403):
                    raise ConfigEntryAuthFailed from err
                raise
            return oauth.token["access_token"]

    opts = entry.options
    api_url = opts.get(CONF_API_URL) or entry.data.get(CONF_API_URL) or DEFAULT_API_URL
    entry.runtime_data = NakordoniData(
        client=NakordoniPlateClient(session, api_url, token),
        regions=[r.lower() for r in _split(opts.get(CONF_REGIONS))],
        min_score=float(opts.get(CONF_MIN_SCORE, DEFAULT_MIN_SCORE)),
        watched=[normalize_plate(p) for p in _split(opts.get(CONF_WATCHED_PLATES))],
        cameras=list(opts.get(CONF_CAMERAS, [])),
        dedupe=Deduper(float(opts.get(CONF_COOLDOWN, DEFAULT_COOLDOWN))),
    )
    data = entry.runtime_data
    # Entries from before v0.4.0 have no mode: they keep the old loose behaviour.
    mode = opts.get(CONF_WATCHED_MODE, WATCHED_LOOSE)
    loose = {normalize_plate(p) for p in _split(opts.get(CONF_LOOSE_PLATES))}
    data.strict_plates = {p for p in data.watched if p and mode == WATCHED_STRICT and p not in loose}
    data.matcher = StrictMatcher(
        float(opts.get(CONF_STRICT_MIN_SCORE, DEFAULT_STRICT_MIN_SCORE)),
        int(opts.get(CONF_STRICT_READS, DEFAULT_STRICT_READS)),
        float(opts.get(CONF_STRICT_WINDOW, DEFAULT_STRICT_WINDOW)),
    )
    gates = list(opts.get(CONF_GATES) or [])
    data.gates = GateController(hass, gates)
    data.fast = camera_fast_modes(opts, data.cameras)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await discovery.async_load_platform(
        hass, Platform.IMAGE_PROCESSING, DOMAIN, {"entry_id": entry.entry_id}, {}
    )
    if opts.get(CONF_FRIGATE_ENABLED):
        from .frigate import async_setup_frigate

        await async_setup_frigate(hass, entry)
    scanner = CameraScanner(hass, entry)
    entry.runtime_data.scanner = scanner
    entry.runtime_data.unsubs.extend(scanner.async_start())
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


def camera_fast_modes(opts: Any, cameras: list[str]) -> dict[str, bool]:
    """Saved per-camera fast mode; default ON for a camera that feeds a gate."""
    saved = opts.get(CONF_FAST_MODE) or {}
    gate_cams: set[str] = set()
    for g in opts.get(CONF_GATES) or []:
        gate_cams.update(gate_cameras(g, cameras))
    return {c: bool(saved.get(c, c in gate_cams)) for c in set(cameras) | set(saved)}


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NakordoniConfigEntry) -> bool:
    for unsub in entry.runtime_data.unsubs:
        unsub()
    for ent in entry.runtime_data.ip_entities:
        await ent.async_remove()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_recognize_and_publish(
    hass: HomeAssistant,
    entry: NakordoniConfigEntry,
    *,
    image: bytes | None = None,
    image_url: str | None = None,
    source: str,
) -> RecognitionResult:
    """Recognize, filter by min score, fire one event per plate, notify entities."""
    data = entry.runtime_data
    extras: dict[str, Any] = {}
    if data.fast.get(source):
        # Skips the vehicle detector (~0.1-0.2 s). An API that ignores it still works.
        extras["config"] = {"mode": "fast", "threshold": data.min_score}
    if source.startswith("camera."):
        extras["camera_id"] = source
        extras["timestamp"] = dt_util.utcnow().isoformat()
    try:
        if image is not None:
            raw = await data.client.recognize_bytes(image, extras=extras or None)
        else:
            raw = await data.client.recognize_url(image_url or "", extras=extras or None)
    except NakordoniAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(f"Nakordoni auth failed: {err}") from err
    except NakordoniError as err:
        raise HomeAssistantError(str(err)) from err
    result = raw.filtered(data.min_score).in_regions(data.regions)
    for r in result.results:
        # Same plate on the same camera inside the cooldown: no second event.
        if not data.dedupe.is_new(source, normalize_plate(str(r.get("plate") or ""))):
            continue
        region = r.get("region") or {}
        hass.bus.async_fire(
            EVENT_VEHICLE_DETECTED,
            {
                "plate": r.get("plate"),
                "confidence": r.get("score"),
                "region_code": region.get("code") if isinstance(region, dict) else None,
                "vehicle_type": (r.get("vehicle") or {}).get("type") if isinstance(r.get("vehicle"), dict) else None,
                "box": r.get("box"),
                "entity_id": source,
                ATTR_CAMERA_ENTITY: source,
            },
        )
    async_dispatcher_send(hass, SIGNAL_RESULT.format(entry.entry_id), source, result)
    await _async_strict(hass, entry, source, raw)
    return result


async def _async_strict(hass: HomeAssistant, entry: NakordoniConfigEntry, source: str, raw: RecognitionResult) -> None:
    """Strict matching on the TOP reading; gates open only from here."""
    data = entry.runtime_data
    if data.matcher is None or not (data.strict_plates or (data.gates and data.gates.gates)):
        return
    match, pending = data.matcher.feed(source, raw.results)
    if match is not None:
        if data.is_strict(match.plate):
            async_dispatcher_send(hass, SIGNAL_STRICT.format(entry.entry_id), match)
        if data.gates is not None:
            # A gate plate always uses strict matching, whatever the sensor mode.
            await data.gates.async_handle(match)
        return
    if pending and data.scanner is not None and source in data.cameras:
        top = data.matcher.current(source)
        if top and data.gates is not None and data.gates.wants(top, source):
            # One read so far: scan again soon so the N-read rule can complete.
            scanner = data.scanner

            @callback
            def _again(_now) -> None:
                entry.async_create_background_task(
                    hass, scanner.async_scan(source, force=True), f"nakordoni_plate strict re-scan {source}"
                )

            data.unsubs.append(async_call_later(hass, STRICT_FOLLOWUP_DELAY, _again))


def result_to_dict(result: RecognitionResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "message": result.message,
        "processing_time": result.processing_time,
        "count": result.count,
        "results": result.results,
    }


@callback
def _register_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_RECOGNIZE):
        return

    async def handle(call: ServiceCall) -> ServiceResponse:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("No loaded nakordoni_plate entry")
        entry = entries[0]
        cams = call.data.get("entity_id") or []
        url = call.data.get(ATTR_IMAGE_URL)
        path = call.data.get(ATTR_FILE_PATH)
        if sum(bool(x) for x in (cams, url, path)) != 1:
            raise ServiceValidationError("Give exactly one of entity_id, image_url, file_path")
        out: dict[str, Any] = {}
        if cams:
            for cam in cams:
                img = await async_get_image(hass, cam)  # upload bytes, never a LAN URL
                out[cam] = result_to_dict(
                    await async_recognize_and_publish(hass, entry, image=img.content, source=cam)
                )
        elif url:
            out[url] = result_to_dict(
                await async_recognize_and_publish(hass, entry, image_url=url, source=url)
            )
        else:
            if not hass.config.is_allowed_path(path):
                raise ServiceValidationError(f"{path} is not in allowlist_external_dirs")
            p = Path(path)

            def _read() -> bytes:
                if p.stat().st_size > MAX_IMAGE_BYTES:
                    raise ServiceValidationError("file larger than 8 MB")
                return p.read_bytes()

            img = await hass.async_add_executor_job(_read)
            out[path] = result_to_dict(
                await async_recognize_and_publish(hass, entry, image=img, source=path)
            )
        return out

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECOGNIZE,
        handle,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.entity_ids,
                vol.Optional(ATTR_IMAGE_URL): cv.url,
                vol.Optional(ATTR_FILE_PATH): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
