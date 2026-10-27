"""OAuth2 PKCE (F1449) via HA application_credentials."""
from __future__ import annotations

from typing import Any

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    return AuthorizationServer(authorize_url=OAUTH2_AUTHORIZE, token_url=OAUTH2_TOKEN)


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    return NakordoniPkceImplementation(hass, auth_domain, credential)


class NakordoniPkceImplementation(AuthImplementation):
    """application_credentials implementation with an S256 PKCE challenge."""

    def __init__(self, hass, auth_domain, credential) -> None:
        super().__init__(
            hass,
            auth_domain,
            credential,
            AuthorizationServer(authorize_url=OAUTH2_AUTHORIZE, token_url=OAUTH2_TOKEN),
        )
        self.code_verifier = config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce.generate_code_verifier(128)

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        return {
            "code_challenge": config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce.compute_code_challenge(
                self.code_verifier
            ),
            "code_challenge_method": "S256",
        }

    @property
    def extra_token_resolve_data(self) -> dict:
        return {"code_verifier": self.code_verifier}
