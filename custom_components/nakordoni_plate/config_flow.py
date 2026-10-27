"""Config + options flow: OAuth2 PKCE (application_credentials) or API key."""
from __future__ import annotations

import logging
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
    CONF_CAMERAS,
    CONF_FRIGATE_ENABLED,
    CONF_FRIGATE_LABELS,
    CONF_FRIGATE_TOPIC,
    CONF_FRIGATE_URL,
    CONF_MIN_SCORE,
    CONF_REGIONS,
    CONF_WATCHED_PLATES,
    DEFAULT_API_URL,
    DEFAULT_FRIGATE_LABELS,
    DEFAULT_FRIGATE_TOPIC,
    DEFAULT_FRIGATE_URL,
    DEFAULT_MIN_SCORE,
    DOMAIN,
)

CONF_API_KEY = "api_key"


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
        return self.async_create_entry(title="Nakordoni Plate Recognition", data=data)

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
    """Regions, min score, watched plates, cameras, Frigate bridge."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_CAMERAS, default=o.get(CONF_CAMERAS, [])): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="camera", multiple=True)
                ),
                vol.Optional(CONF_REGIONS, default=o.get(CONF_REGIONS, "")): str,
                vol.Optional(CONF_MIN_SCORE, default=o.get(CONF_MIN_SCORE, DEFAULT_MIN_SCORE)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=1, step=0.01, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_WATCHED_PLATES, default=o.get(CONF_WATCHED_PLATES, "")): str,
                vol.Optional(CONF_API_URL, default=o.get(CONF_API_URL, self.config_entry.data.get(CONF_API_URL, DEFAULT_API_URL))): str,
                vol.Optional(CONF_FRIGATE_ENABLED, default=o.get(CONF_FRIGATE_ENABLED, False)): bool,
                vol.Optional(CONF_FRIGATE_URL, default=o.get(CONF_FRIGATE_URL, DEFAULT_FRIGATE_URL)): str,
                vol.Optional(CONF_FRIGATE_TOPIC, default=o.get(CONF_FRIGATE_TOPIC, DEFAULT_FRIGATE_TOPIC)): str,
                vol.Optional(CONF_FRIGATE_LABELS, default=o.get(CONF_FRIGATE_LABELS, DEFAULT_FRIGATE_LABELS)): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
