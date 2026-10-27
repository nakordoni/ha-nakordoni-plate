"""Auto-scan: motion + interval modes, cooldown, dedupe, defaults, options flow."""
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.components.camera import Image
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.nakordoni_plate.const import DEFAULT_API_URL as URL, DOMAIN, EVENT_VEHICLE_DETECTED
from custom_components.nakordoni_plate.scanner import default_camera_scan

OK = {"success": True, "results": [{"plate": "AA1234BB", "score": 0.93, "region": None, "vehicle": None}]}
CAM = "camera.drive"
IMG = "custom_components.nakordoni_plate.scanner.async_get_image"


async def _setup(hass, aioclient_mock, scan, cooldown=10):
    aioclient_mock.post(URL, json=OK)
    hass.states.async_set(CAM, "idle")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"auth_type": "api_key", "api_key": "k", "api_url": URL},
        options={"cameras": [CAM], "cooldown": cooldown, "camera_scan": {CAM: scan}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    events = []
    hass.bus.async_listen(EVENT_VEHICLE_DETECTED, lambda e: events.append(e))
    return entry, events


async def test_motion_mode_scans_on_off_to_on(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    hass.states.async_set("binary_sensor.drive_motion", "off")
    entry, events = await _setup(hass, aioclient_mock, {"scan_mode": "motion", "motion_entity": "binary_sensor.drive_motion"})
    with patch(IMG, AsyncMock(return_value=Image("image/jpeg", b"jpg"))):
        hass.states.async_set("binary_sensor.drive_motion", "on")
        await hass.async_block_till_done()
        assert aioclient_mock.call_count == 1 and len(events) == 1
        assert hass.states.get("image_processing.nakordoni_plate_drive").state == "1"
        # on -> on attribute change and unavailable -> on are not edges
        hass.states.async_set("binary_sensor.drive_motion", "on", {"x": 1})
        hass.states.async_set("binary_sensor.drive_motion", "unavailable")
        hass.states.async_set("binary_sensor.drive_motion", "on")
        await hass.async_block_till_done()
        assert aioclient_mock.call_count == 1
        # cooldown: a new edge 3 s later is skipped, 11 s later it scans
        freezer.tick(3)
        hass.states.async_set("binary_sensor.drive_motion", "off")
        hass.states.async_set("binary_sensor.drive_motion", "on")
        await hass.async_block_till_done()
        assert aioclient_mock.call_count == 1
        freezer.tick(11)
        hass.states.async_set("binary_sensor.drive_motion", "off")
        hass.states.async_set("binary_sensor.drive_motion", "on")
        await hass.async_block_till_done()
        assert aioclient_mock.call_count == 2 and len(events) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_interval_mode_and_unavailable_camera(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    entry, events = await _setup(hass, aioclient_mock, {"scan_mode": "interval", "scan_interval": 5}, cooldown=0)
    with patch(IMG, AsyncMock(return_value=Image("image/jpeg", b"jpg"))):
        freezer.tick(timedelta(seconds=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert aioclient_mock.call_count == 1
        hass.states.async_set(CAM, "unavailable")
        freezer.tick(timedelta(seconds=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert aioclient_mock.call_count == 1  # camera unavailable: no API call
        hass.states.async_set(CAM, "idle")
        freezer.tick(timedelta(seconds=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert aioclient_mock.call_count == 2
    assert len(events) == 2  # cooldown 0 -> dedupe window 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dedupe_same_plate_same_camera(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    entry, events = await _setup(hass, aioclient_mock, {"scan_mode": "off"}, cooldown=10)
    ent = entry.runtime_data.ip_entities[0]
    await ent.async_process_image(b"a")
    freezer.tick(4)
    await ent.async_process_image(b"b")  # same plate, 4 s later: no event
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2 and len(events) == 1
    freezer.tick(11)
    await ent.async_process_image(b"c")
    await hass.async_block_till_done()
    assert len(events) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_off_mode_no_scans(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    entry, _ = await _setup(hass, aioclient_mock, {"scan_mode": "off"})
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_default_mode_detects_motion_sensor(hass: HomeAssistant) -> None:
    cfg = MockConfigEntry(domain="demo")
    cfg.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("demo", "cam1")})
    reg = er.async_get(hass)
    reg.async_get_or_create("camera", "demo", "c1", device_id=dev.id, suggested_object_id="cam1")
    assert default_camera_scan(hass, "camera.cam1")["scan_mode"] == "interval"
    assert default_camera_scan(hass, "camera.cam1")["scan_interval"] == 30
    reg.async_get_or_create("binary_sensor", "demo", "m1", device_id=dev.id, suggested_object_id="cam1_motion",
                            original_device_class="motion")
    d = default_camera_scan(hass, "camera.cam1")
    assert d["scan_mode"] == "motion" and d["motion_entity"] == "binary_sensor.cam1_motion"
    assert default_camera_scan(hass, "camera.nodevice")["scan_mode"] == "interval"


async def test_options_flow_per_camera(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"auth_type": "api_key", "api_key": "k", "api_url": URL})
    entry.add_to_hass(hass)
    with patch("custom_components.nakordoni_plate.async_setup_entry", return_value=True):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"cameras": ["camera.a", "camera.b"], "cooldown": 15})
        assert r["step_id"] == "camera" and "camera.a" in r["description_placeholders"]["camera"]
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"scan_mode": "motion"})
        assert r["errors"] == {"motion_entity": "motion_required"}
        r = await hass.config_entries.options.async_configure(
            r["flow_id"], {"scan_mode": "motion", "motion_entity": "binary_sensor.a_motion"})
        assert "camera.b" in r["description_placeholders"]["camera"]
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"scan_mode": "interval", "scan_interval": 10})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"]["camera_scan"] == {
        "camera.a": {"scan_mode": "motion", "motion_entity": "binary_sensor.a_motion", "scan_interval": 30},
        "camera.b": {"scan_mode": "interval", "motion_entity": None, "scan_interval": 10},
    }
    assert r["data"]["cooldown"] == 15
