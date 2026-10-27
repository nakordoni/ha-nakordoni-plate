"""v0.4.0: strict watched-plate mode, gate action, kill switch, fast mode."""
from datetime import datetime, time
from unittest.mock import AsyncMock, patch

from homeassistant.components.camera import Image
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.nakordoni_plate.const import DEFAULT_API_URL as URL, DOMAIN, EVENT_GATE_OPENED
from custom_components.nakordoni_plate.gate import StrictMatcher, in_schedule

CAM = "camera.gate"
IMG = "custom_components.nakordoni_plate.scanner.async_get_image"


def res(plate, score=0.95, cands=None):
    return {"success": True, "results": [{"plate": plate, "score": score, "candidates": cands or []}]}


def gate(entity="cover.gate", **kw):
    g = {"id": "g1", "name": "Front", "plates": ["AA1234BB"], "entity": entity, "cameras": [],
         "start": None, "end": None, "cooldown": 60}
    g.update(kw)
    return g


async def _setup(hass, aioclient_mock, response, gates=None, mode="strict", extra=None):
    aioclient_mock.post(URL, json=response)
    hass.states.async_set(CAM, "idle")
    opts = {"cameras": [CAM], "cooldown": 0, "camera_scan": {CAM: {"scan_mode": "off"}},
            "watched_plates": "AA-1234 BB", "watched_mode": mode, "gates": gates or []}
    opts.update(extra or {})
    entry = MockConfigEntry(domain=DOMAIN, data={"auth_type": "api_key", "api_key": "k", "api_url": URL}, options=opts)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    opened = []
    hass.bus.async_listen(EVENT_GATE_OPENED, lambda e: opened.append(e.data))
    return entry, opened


async def _scan(hass, entry, n=1, freezer=None):
    with patch(IMG, AsyncMock(return_value=Image("image/jpeg", b"jpg"))):
        for _ in range(n):
            await entry.runtime_data.scanner.async_scan(CAM, force=True)
            await hass.async_block_till_done()
            if freezer:
                freezer.tick(2)


SENSOR = "binary_sensor.nakordoni_watched_aa1234bb"


def test_matcher_consecutive_window_score(freezer) -> None:
    m = StrictMatcher(0.9, 2, 15)
    top = [{"plate": "aa 1234-bb", "score": 0.95}]
    assert m.feed("c", top) == (None, True)
    freezer.tick(5)
    match, _ = m.feed("c", top)
    assert match.plate == "AA1234BB"
    # low score breaks the streak
    m2 = StrictMatcher(0.9, 2, 15)
    m2.feed("c", top)
    m2.feed("c", [{"plate": "AA1234BB", "score": 0.89}])
    assert m2.feed("c", top) == (None, True)
    # outside the window: no match
    m3 = StrictMatcher(0.9, 2, 15)
    m3.feed("c", top)
    freezer.tick(16)
    assert m3.feed("c", top)[0] is None
    # a different plate in between breaks it; the top reading is the highest score
    m4 = StrictMatcher(0.9, 2, 15)
    m4.feed("c", top)
    m4.feed("c", [{"plate": "ZZ9", "score": 0.99}, {"plate": "AA1234BB", "score": 0.95}])
    assert m4.feed("c", top)[0] is None


def test_schedule() -> None:
    day = datetime(2026, 10, 27, 12, 0)
    night = datetime(2026, 10, 27, 23, 30)
    assert in_schedule(gate(), day)
    assert in_schedule(gate(start="08:00:00", end="18:00:00"), day)
    assert not in_schedule(gate(start="08:00:00", end="18:00:00"), night)
    assert in_schedule(gate(start="22:00:00", end="06:00:00"), night)
    assert not in_schedule(gate(start="22:00:00", end="06:00:00"), day)
    assert in_schedule(gate(start=time(11, 0)), day)


async def test_strict_vs_loose_sensor(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234BB"))
    await _scan(hass, entry)
    assert hass.states.get(SENSOR).state == "off"  # one read is not enough
    await _scan(hass, entry)
    assert hass.states.get(SENSOR).state == "on"
    assert hass.states.get(SENSOR).attributes["mode"] == "strict"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_loose_mode_candidate_counts(hass: HomeAssistant, aioclient_mock) -> None:
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234B8", 0.7, [{"plate": "AA1234BB"}]), mode="loose")
    await _scan(hass, entry)
    assert hass.states.get(SENSOR).state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_strict_candidate_never_opens(hass: HomeAssistant, aioclient_mock) -> None:
    calls = async_mock_service(hass, "cover", "open_cover")
    entry, opened = await _setup(hass, aioclient_mock, res("AA1234B8", 0.99, [{"plate": "AA1234BB", "score": 0.98}]),
                                 gates=[gate()])
    await _scan(hass, entry, 3)
    assert hass.states.get(SENSOR).state == "off"
    assert not calls and not opened
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_score_threshold_blocks_gate(hass: HomeAssistant, aioclient_mock) -> None:
    calls = async_mock_service(hass, "cover", "open_cover")
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234BB", 0.85), gates=[gate()])
    await _scan(hass, entry, 3)
    assert not calls
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_gate_opens_event_logbook_cooldown(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    calls = async_mock_service(hass, "cover", "open_cover")
    logbook = []
    hass.bus.async_listen("logbook_entry", lambda e: logbook.append(e.data))
    entry, opened = await _setup(hass, aioclient_mock, res("AA1234BB"), gates=[gate()])
    await _scan(hass, entry, 2)
    assert len(calls) == 1 and calls[0].data["entity_id"] == "cover.gate"
    assert opened == [{"plate": "AA1234BB", "camera": CAM, "gate": "cover.gate", "score": 0.95}]
    assert logbook and "AA1234BB" in logbook[0]["message"]
    await _scan(hass, entry, 3, freezer)  # inside the 60 s cooldown
    assert len(calls) == 1
    freezer.tick(61)
    await _scan(hass, entry, 2)  # old reads are outside the window: 2 new reads
    assert len(calls) == 2
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_gate_schedule_and_cameras(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    freezer.move_to("2026-10-27 20:00:00+00:00")  # midday in the test time zone
    calls = async_mock_service(hass, "cover", "open_cover")
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234BB"), gates=[
        gate(id="night", start="22:00:00", end="05:00:00"),
        gate(id="other", cameras=["camera.other"]),
    ])
    await _scan(hass, entry, 3)
    assert not calls
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_kill_switch(hass: HomeAssistant, aioclient_mock) -> None:
    calls = async_mock_service(hass, "cover", "open_cover")
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234BB"), gates=[gate()])
    sw = "switch.nakordoni_gate_automation_enabled_front"
    assert hass.states.get(sw).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    assert hass.states.get(sw).state == "off"
    await _scan(hass, entry, 3)
    assert not calls
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    await _scan(hass, entry, 1)
    assert len(calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_each_gate_domain(hass: HomeAssistant, aioclient_mock) -> None:
    ents = ["cover.g", "switch.g", "button.g", "lock.g"]
    gates = [gate(entity=e, id=f"g{i}", name=f"G{i}") for i, e in enumerate(ents)]
    entry, opened = await _setup(hass, aioclient_mock, res("AA1234BB"), gates=gates)
    # mocked after setup: the switch platform registers its own switch services
    mocks = {
        "cover.g": async_mock_service(hass, "cover", "open_cover"),
        "switch.g": async_mock_service(hass, "switch", "turn_on"),
        "button.g": async_mock_service(hass, "button", "press"),
        "lock.g": async_mock_service(hass, "lock", "unlock"),
    }
    await _scan(hass, entry, 2)
    for ent, calls in mocks.items():
        assert len(calls) == 1 and calls[0].data["entity_id"] == ent
    assert {o["gate"] for o in opened} == set(mocks)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_fast_mode_sent_for_gate_camera(hass: HomeAssistant, aioclient_mock) -> None:
    async_mock_service(hass, "cover", "open_cover")
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234BB"), gates=[gate()])
    assert entry.runtime_data.fast == {CAM: True}
    await _scan(hass, entry)
    form = aioclient_mock.mock_calls[-1][2]
    fields = {f[0]["name"]: f[2] for f in form._fields}
    assert fields["config"] == '{"mode": "fast", "threshold": 0.6}'
    assert fields["camera_id"] == CAM and "timestamp" in fields
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_fast_mode_off_and_api_400_fallback(hass: HomeAssistant, aioclient_mock) -> None:
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234BB"), extra={"fast_mode": {CAM: False}})
    await _scan(hass, entry)
    fields = {f[0]["name"] for f in aioclient_mock.mock_calls[-1][2]._fields}
    assert "config" not in fields
    assert await hass.config_entries.async_unload(entry.entry_id)
    # An older API that answers 400 to the new fields: one retry without them.
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from custom_components.nakordoni_plate.api import NakordoniPlateClient

    aioclient_mock.clear_requests()
    aioclient_mock.post(URL, status=400, json={"code": "unknown_field"})

    async def tok():
        return "k"

    c = NakordoniPlateClient(async_get_clientsession(hass), URL, tok)
    import pytest
    from custom_components.nakordoni_plate.api import NakordoniError

    with pytest.raises(NakordoniError):
        await c.recognize_url("https://x/y.jpg", extras={"config": {"mode": "fast"}})
    assert aioclient_mock.call_count == 2
    assert "config" not in aioclient_mock.mock_calls[1][2]


async def test_motion_followup_scan_completes_streak(hass: HomeAssistant, aioclient_mock, freezer) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    calls = async_mock_service(hass, "cover", "open_cover")
    entry, _ = await _setup(hass, aioclient_mock, res("AA1234BB"), gates=[gate()], extra={"cooldown": 30})
    with patch(IMG, AsyncMock(return_value=Image("image/jpeg", b"jpg"))):
        await entry.runtime_data.scanner.async_scan(CAM)
        await hass.async_block_till_done()
        assert not calls
        freezer.tick(1.5)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len(calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_options_flow_gate_and_new_install_strict(hass: HomeAssistant, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"auth_type": "api_key", "api_key": "k", "api_url": URL})
    entry.add_to_hass(hass)
    with patch("custom_components.nakordoni_plate.async_setup_entry", return_value=True):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(
            r["flow_id"], {"cameras": [CAM], "watched_plates": "AA1234BB", "configure_gates": True})
        assert r["step_id"] == "gates"
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"add_gate": True})
        assert r["step_id"] == "gate"
        r = await hass.config_entries.options.async_configure(
            r["flow_id"], {"name": "Front", "plates": ["aa 1234 bb"], "entity": "lock.front", "start": "07:00:00"})
        assert r["step_id"] == "gates"
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"add_gate": False})
        assert r["step_id"] == "camera"
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"scan_mode": "interval"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    g = r["data"]["gates"][0]
    assert g["plates"] == ["AA1234BB"] and g["entity"] == "lock.front" and g["cooldown"] == 60
    assert r["data"]["fast_mode"] == {CAM: True}  # default ON for a gate camera
    assert r["data"]["watched_mode"] == "strict"
