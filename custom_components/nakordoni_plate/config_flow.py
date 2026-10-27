"""Config + options flow: OAuth2 PKCE (application_credentials) or API key."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NakordoniAuthError, NakordoniError, NakordoniPlateClient
from .const import (
    AUTH_API_KEY,
    AUTH_OAUTH,
    CONF_API_URL,
    CONF_AUTH_TYPE,
    CONF_CAMERA_SCAN,
    CONF_CAMERAS,
    CONF_CONFIGURE_GATES,
    CONF_COOLDOWN,
    CONF_FAST_MODE,
    CONF_GATE_CAMERAS,
    CONF_GATE_COOLDOWN,
    CONF_GATE_END,
    CONF_GATE_ENTITY,
    CONF_GATE_ID,
    CONF_GATE_NAME,
    CONF_GATE_PLATES,
    CONF_GATE_START,
    CONF_GATES,
    CONF_LOOSE_PLATES,
    CONF_STRICT_MIN_SCORE,
    CONF_STRICT_READS,
    CONF_STRICT_WINDOW,
    CONF_WATCHED_MODE,
    DEFAULT_GATE_COOLDOWN,
    DEFAULT_STRICT_MIN_SCORE,
    DEFAULT_STRICT_READS,
    DEFAULT_STRICT_WINDOW,
    GATE_ACTIONS,
    WATCHED_LOOSE,
    WATCHED_MODES,
    WATCHED_STRICT,
    CONF_FRIGATE_ENABLED,
    CONF_FRIGATE_LABELS,
    CONF_FRIGATE_TOPIC,
    CONF_FRIGATE_URL,
    CONF_MIN_SCORE,
    CONF_MOTION_ENTITY,
    CONF_REGIONS,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_MODE,
    CONF_WATCHED_PLATES,
    DEFAULT_API_URL,
    DEFAULT_COOLDOWN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_FRIGATE_LABELS,
    DEFAULT_FRIGATE_TOPIC,
    DEFAULT_FRIGATE_URL,
    DEFAULT_MIN_SCORE,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    SCAN_MODES,
    SCAN_MOTION,
)
from .api import normalize_plate
from .gate import gate_cameras
from .scanner import camera_scan_config

CONF_API_KEY = "api_key"
# New installs get strict watched-plate mode; older entries keep loose until changed.
NEW_INSTALL_OPTIONS = {CONF_WATCHED_MODE: WATCHED_STRICT}
CONF_KEEP_GATES = "keep_gates"
CONF_ADD_GATE = "add_gate"


def _plates(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    return [p for p in (normalize_plate(str(v)) for v in raw) if p]


class NakordoniPlateConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle a config flow."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(__name__)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["oauth", "api_key"])

    async def async_step_oauth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        data[CONF_AUTH_TYPE] = AUTH_OAUTH
        data[CONF_API_URL] = DEFAULT_API_URL
        await self.async_set_unique_id(f"oauth_{data['token'].get('access_token', '')[-12:]}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Nakordoni Plate Recognition", data=data, options=NEW_INSTALL_OPTIONS)

    async def async_step_api_key(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_API_KEY].strip()
            url = user_input.get(CONF_API_URL) or DEFAULT_API_URL

            async def _tok() -> str:
                return key

            client = NakordoniPlateClient(async_get_clientsession(self.hass), url, _tok)
            try:
                await client.validate()
            except NakordoniAuthError:
                errors["base"] = "invalid_auth"
            except NakordoniError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"key_{key[-12:]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Nakordoni Plate Recognition (API key)",
                    data={CONF_AUTH_TYPE: AUTH_API_KEY, CONF_API_KEY: key, CONF_API_URL: url},
                    options=NEW_INSTALL_OPTIONS,
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): str,
                vol.Optional(CONF_API_URL, default=DEFAULT_API_URL): str,
            }
        )
        return self.async_show_form(step_id="api_key", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NakordoniOptionsFlow()


class NakordoniOptionsFlow(OptionsFlow):
    """Step 1: cameras, filters, cooldown, Frigate. Step 2: one scan-mode form per camera."""

    def __init__(self) -> None:
        self._opts: dict[str, Any] = {}
        self._pending: list[str] = []
        self._scan: dict[str, dict[str, Any]] = {}
        self._fast: dict[str, bool] = {}
        self._gates: list[dict[str, Any]] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts = dict(user_input)
            self._pending = list(user_input.get(CONF_CAMERAS, []))
            self._gates = [dict(g) for g in self.config_entry.options.get(CONF_GATES) or []]
            if self._opts.pop(CONF_CONFIGURE_GATES, False):
                return await self.async_step_gates()
            return await self.async_step_camera()
        o = self.config_entry.options
        mode_default = o.get(CONF_WATCHED_MODE, WATCHED_LOOSE if o.get(CONF_WATCHED_PLATES) else WATCHED_STRICT)
        schema = vol.Schema(
            {
                vol.Optional(CONF_CAMERAS, default=o.get(CONF_CAMERAS, [])): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="camera", multiple=True)
                ),
                vol.Optional(CONF_COOLDOWN, default=o.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=3600, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_REGIONS, default=o.get(CONF_REGIONS, "")): str,
                vol.Optional(CONF_MIN_SCORE, default=o.get(CONF_MIN_SCORE, DEFAULT_MIN_SCORE)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=1, step=0.01, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_WATCHED_PLATES, default=o.get(CONF_WATCHED_PLATES, "")): str,
                vol.Optional(CONF_WATCHED_MODE, default=mode_default): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=WATCHED_MODES, translation_key="watched_mode", mode=selector.SelectSelectorMode.LIST)
                ),
                vol.Optional(CONF_LOOSE_PLATES, default=o.get(CONF_LOOSE_PLATES, "")): str,
                vol.Optional(CONF_STRICT_MIN_SCORE, default=o.get(CONF_STRICT_MIN_SCORE, DEFAULT_STRICT_MIN_SCORE)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.5, max=1, step=0.01, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_STRICT_READS, default=o.get(CONF_STRICT_READS, DEFAULT_STRICT_READS)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_STRICT_WINDOW, default=o.get(CONF_STRICT_WINDOW, DEFAULT_STRICT_WINDOW)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=300, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_CONFIGURE_GATES, default=False): bool,
                vol.Optional(CONF_API_URL, default=o.get(CONF_API_URL, self.config_entry.data.get(CONF_API_URL, DEFAULT_API_URL))): str,
                vol.Optional(CONF_FRIGATE_ENABLED, default=o.get(CONF_FRIGATE_ENABLED, False)): bool,
                vol.Optional(CONF_FRIGATE_URL, default=o.get(CONF_FRIGATE_URL, DEFAULT_FRIGATE_URL)): str,
                vol.Optional(CONF_FRIGATE_TOPIC, default=o.get(CONF_FRIGATE_TOPIC, DEFAULT_FRIGATE_TOPIC)): str,
                vol.Optional(CONF_FRIGATE_LABELS, default=o.get(CONF_FRIGATE_LABELS, DEFAULT_FRIGATE_LABELS)): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_camera(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cam = self._pending[0]
            if user_input.get(CONF_SCAN_MODE) == SCAN_MOTION and not user_input.get(CONF_MOTION_ENTITY):
                errors[CONF_MOTION_ENTITY] = "motion_required"
            else:
                self._scan[cam] = {
                    CONF_SCAN_MODE: user_input[CONF_SCAN_MODE],
                    CONF_MOTION_ENTITY: user_input.get(CONF_MOTION_ENTITY),
                    CONF_SCAN_INTERVAL: int(user_input.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL),
                }
                if CONF_FAST_MODE in user_input:
                    self._fast[cam] = bool(user_input[CONF_FAST_MODE])
                self._pending.pop(0)
        if not self._pending:
            data = {**self._opts, CONF_CAMERA_SCAN: self._scan}
            if self._gates:
                data[CONF_GATES] = self._gates
            if self._fast:
                data[CONF_FAST_MODE] = self._fast
            return self.async_create_entry(data=data)
        cam = self._pending[0]
        cfg = camera_scan_config(self.hass, dict(self.config_entry.options), cam)
        motion = {"suggested_value": cfg.get(CONF_MOTION_ENTITY)} if cfg.get(CONF_MOTION_ENTITY) else {}
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_MODE, default=cfg[CONF_SCAN_MODE]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=SCAN_MODES, translation_key="scan_mode", mode=selector.SelectSelectorMode.LIST)
                ),
                vol.Optional(CONF_MOTION_ENTITY, description=motion): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Optional(CONF_SCAN_INTERVAL, default=cfg.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=MIN_SCAN_INTERVAL, max=86400, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_FAST_MODE, default=self._fast_default(cam)): bool,
            }
        )
        state = self.hass.states.get(cam)
        name = state.name if state else cam
        return self.async_show_form(
            step_id="camera", data_schema=schema, errors=errors,
            description_placeholders={"camera": f"{name} ({cam})"},
        )

    def _fast_default(self, cam: str) -> bool:
        saved = self.config_entry.options.get(CONF_FAST_MODE) or {}
        if cam in saved:
            return bool(saved[cam])
        cams = list(self._opts.get(CONF_CAMERAS, []))
        return any(cam in gate_cameras(g, cams) for g in self._gates)

    async def async_step_gates(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Keep / remove existing gates, or add one."""
        if user_input is not None:
            keep = set(user_input.get(CONF_KEEP_GATES) or [])
            self._gates = [g for g in self._gates if g[CONF_GATE_ID] in keep]
            if user_input.get(CONF_ADD_GATE):
                return await self.async_step_gate()
            return await self.async_step_camera()
        options = [
            selector.SelectOptionDict(value=g[CONF_GATE_ID], label=g.get(CONF_GATE_NAME) or g[CONF_GATE_ENTITY])
            for g in self._gates
        ]
        schema: dict[Any, Any] = {}
        if options:
            schema[vol.Optional(CONF_KEEP_GATES, default=[o["value"] for o in options])] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=True)
            )
        schema[vol.Optional(CONF_ADD_GATE, default=not options)] = bool
        return self.async_show_form(step_id="gates", data_schema=vol.Schema(schema))

    async def async_step_gate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One gate: plates, entity, cameras, schedule, cooldown. Opens on strict matches only."""
        errors: dict[str, str] = {}
        watched = _plates(self._opts.get(CONF_WATCHED_PLATES))
        if user_input is not None:
            plates = _plates(user_input.get(CONF_GATE_PLATES))
            entity = user_input.get(CONF_GATE_ENTITY) or ""
            if not plates:
                errors[CONF_GATE_PLATES] = "plates_required"
            elif entity.split(".", 1)[0] not in GATE_ACTIONS:
                errors[CONF_GATE_ENTITY] = "gate_entity_domain"
            else:
                self._gates.append(
                    {
                        CONF_GATE_ID: uuid.uuid4().hex[:8],
                        CONF_GATE_NAME: (user_input.get(CONF_GATE_NAME) or "").strip(),
                        CONF_GATE_PLATES: plates,
                        CONF_GATE_ENTITY: entity,
                        CONF_GATE_CAMERAS: list(user_input.get(CONF_GATE_CAMERAS) or []),
                        CONF_GATE_START: user_input.get(CONF_GATE_START) or None,
                        CONF_GATE_END: user_input.get(CONF_GATE_END) or None,
                        CONF_GATE_COOLDOWN: int(user_input.get(CONF_GATE_COOLDOWN, DEFAULT_GATE_COOLDOWN)),
                    }
                )
                return await self.async_step_gates()
        cams = list(self._opts.get(CONF_CAMERAS, []))
        schema = vol.Schema(
            {
                vol.Optional(CONF_GATE_NAME, default=""): str,
                vol.Required(CONF_GATE_PLATES): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=watched, multiple=True, custom_value=True)
                ),
                vol.Required(CONF_GATE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=list(GATE_ACTIONS))
                ),
                vol.Optional(CONF_GATE_CAMERAS, default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=cams, multiple=True)
                ),
                vol.Optional(CONF_GATE_START): selector.TimeSelector(),
                vol.Optional(CONF_GATE_END): selector.TimeSelector(),
                vol.Optional(CONF_GATE_COOLDOWN, default=DEFAULT_GATE_COOLDOWN): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=3600, step=1, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="gate", data_schema=schema, errors=errors)
