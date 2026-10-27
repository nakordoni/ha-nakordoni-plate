"""Config-flow, API client, service, entity tests (API mocked)."""
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nakordoni_plate.api import NakordoniAuthError, NakordoniPlateClient
from custom_components.nakordoni_plate.const import (
    DEFAULT_API_URL,
    DOMAIN,
    EVENT_VEHICLE_DETECTED,
    OAUTH2_AUTHORIZE,
)

URL = DEFAULT_API_URL
OK = {
    "success": True, "message": "ok", "processing_time": 0.12,
    "results": [{
        "plate": "AA1234BB", "score": 0.93,
        "candidates": [{"plate": "AA1234BB", "score": 0.93}, {"plate": "AA1234B8", "score": 0.6}],
        "region": None, "vehicle": None, "dscore": 0.88,
        "box": {"xmin": 1, "ymin": 2, "xmax": 30, "ymax": 40},
    }, {"plate": "LOW1", "score": 0.2, "region": None, "vehicle": None}],
}


async def test_menu_and_api_key_ok(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, status=400, json={"code": "missing_image"})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.MENU
    assert set(r["menu_options"]) == {"oauth", "api_key"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "api_key"})
    assert r["step_id"] == "api_key"
    with patch("custom_components.nakordoni_plate.async_setup_entry", return_value=True):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "k_123", "api_url": URL})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"]["auth_type"] == "api_key"
    assert aioclient_mock.mock_calls[0][3]["X-API-Key"] == "k_123"


async def test_api_key_invalid(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, status=401, json={"message": "bad key"})
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "api_key"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "bad"})
    assert r["type"] is FlowResultType.FORM
    assert r["errors"] == {"base": "invalid_auth"}


async def test_oauth_pkce_flow(hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host) -> None:
    from homeassistant.components.application_credentials import ClientCredential, async_import_client_credential
    from homeassistant.helpers import config_entry_oauth2_flow

    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(hass, DOMAIN, ClientCredential("cid", "csecret"), "cred")
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "oauth"})
    assert r["type"] is FlowResultType.EXTERNAL_STEP
    assert r["url"].startswith(OAUTH2_AUTHORIZE)
    assert "code_challenge_method=S256" in r["url"] and "code_challenge=" in r["url"]
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": r["flow_id"], "redirect_uri": "https://example.com/auth/external/callback"}
    )
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abc&state={state}")
    assert resp.status == 200
    aioclient_mock.post(
        "https://api.nakordoni.dev/oauth/token",
        json={"access_token": "devapi_key_abcdef123456", "refresh_token": "rt", "expires_in": 2592000, "token_type": "Bearer"},
    )
    with patch("custom_components.nakordoni_plate.async_setup_entry", return_value=True):
        r = await hass.config_entries.flow.async_configure(r["flow_id"])
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"]["auth_type"] == "oauth"
    token_body = aioclient_mock.mock_calls[-1][2]
    assert "code_verifier" in token_body


async def test_client_404_is_zero(hass: HomeAssistant, aioclient_mock) -> None:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(URL, status=404, json={"code": "no_plate_detected"})

    async def tok():
        return "k"

    c = NakordoniPlateClient(async_get_clientsession(hass), URL, tok)
    res = await c.recognize_bytes(b"jpg")
    assert res.count == 0 and res.success


async def test_client_auth_error(hass: HomeAssistant, aioclient_mock) -> None:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    import pytest

    aioclient_mock.post(URL, status=403, json={})

    async def tok():
        return "k"

    c = NakordoniPlateClient(async_get_clientsession(hass), URL, tok)
    with pytest.raises(NakordoniAuthError):
        await c.recognize_url("https://x/y.jpg")


async def test_setup_service_entities_event(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(URL, json=OK)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"auth_type": "api_key", "api_key": "k", "api_url": URL},
        options={"cameras": ["camera.drive"], "min_score": 0.5, "watched_plates": "aa 1234 bb, ZZ9"},
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "camera", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    events = []
    hass.bus.async_listen(EVENT_VEHICLE_DETECTED, lambda e: events.append(e))

    ent = entry.runtime_data.ip_entities[0]
    assert ent.entity_id == "image_processing.nakordoni_plate_drive"
    assert ent is not None
    await ent.async_process_image(b"jpegbytes")
    ent.async_write_ha_state()
    await hass.async_block_till_done()

    st = hass.states.get("image_processing.nakordoni_plate_drive")
    assert st.state == "1"  # LOW1 filtered by min_score
    assert st.attributes["vehicles"][0]["plate"] == "AA1234BB"
    assert st.attributes["vehicles"][0]["region_code"] is None
    assert hass.states.get("sensor.nakordoni_last_plate_drive").state == "AA1234BB"
    assert hass.states.get("binary_sensor.nakordoni_watched_aa1234bb").state == "on"
    assert hass.states.get("binary_sensor.nakordoni_watched_zz9").state == "off"
    assert len(events) == 1 and events[0].data["entity_id"] == "camera.drive"

    resp = await hass.services.async_call(
        DOMAIN, "recognize", {"image_url": "https://example.com/c.jpg"}, blocking=True, return_response=True
    )
    assert resp["https://example.com/c.jpg"]["count"] == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_region_filter_local() -> None:
    from custom_components.nakordoni_plate.api import RecognitionResult

    res = RecognitionResult(True, None, None, [
        {"plate": "A", "region": {"code": "pl", "score": 0.9}},
        {"plate": "B", "region": {"code": "DE", "score": 0.9}},
        {"plate": "C", "region": None},
    ])
    assert [r["plate"] for r in res.in_regions(["PL", "ua"]).results] == ["A", "C"]
    assert res.in_regions([]).count == 3


async def test_bundled_client_goes_to_authorize(hass: HomeAssistant, current_request_with_host) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "oauth"})
    assert r["type"] is FlowResultType.EXTERNAL_STEP
    assert "client_id=nkd_3f595417301a83ff4ed4dd47" in r["url"]
