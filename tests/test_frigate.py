import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from custom_components.nakordoni_plate.const import DEFAULT_API_URL, DOMAIN

FRIGATE = "http://frigate.local:5000"


async def test_frigate_end_event_sets_sub_label(hass: HomeAssistant, mqtt_mock, aioclient_mock) -> None:
    aioclient_mock.get(f"{FRIGATE}/api/events/ev1/snapshot.jpg", content=b"jpg")
    aioclient_mock.post(DEFAULT_API_URL, json={"success": True, "results": [{"plate": "AB123CD", "score": 0.9, "region": None, "vehicle": None}]})
    aioclient_mock.post(f"{FRIGATE}/api/events/ev1/sub_label", json={"success": True})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"auth_type": "api_key", "api_key": "k", "api_url": DEFAULT_API_URL},
        options={"frigate_enabled": True, "frigate_url": FRIGATE},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, "frigate/events", json.dumps({"type": "new", "after": {"id": "ev1", "label": "car", "camera": "gate"}}))
    async_fire_mqtt_message(hass, "frigate/events", json.dumps({"type": "end", "after": {"id": "ev1", "label": "car", "camera": "gate", "has_snapshot": True}}))
    await hass.async_block_till_done()
    sub = [c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/sub_label")]
    assert len(sub) == 1 and sub[0][2] == {"subLabel": "AB123CD", "subLabelScore": 0.9}
