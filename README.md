# Nakordoni Plate Recognition for Home Assistant

A custom integration for the Nakordoni DevAPI plate recognition product. It can be installed with HACS.
The entity and event shapes follow the well-known `platerecognizer` integration. To port an existing automation, rename it as follows:
`platerecognizer.vehicle_detected` → `nakordoni_plate_vehicle_detected`, `image_processing.platerecognizer_*` → `image_processing.nakordoni_plate_*`.

## Quick start: install, sign in, pick cameras
1. **Install.** HACS → search *Nakordoni Plate Recognition* → Download (or HACS → Custom repositories → this repo, category Integration). As an alternative, copy `custom_components/nakordoni_plate` into `/config/custom_components/`. Restart Home Assistant.
2. **Sign in.** Settings → Devices & services → Add → *Nakordoni Plate Recognition*. Select one of these methods:
   - **Sign in with Nakordoni (OAuth)**: OAuth2 with PKCE, redirect through `https://my.home-assistant.io/redirect/oauth`. The integration includes the first-party public client (`nkd_3f595417301a83ff4ed4dd47`), so you do not need your own credentials. If it does not show, add it under Settings → Application credentials (any secret; the server does not check it).
   - **Use a DevAPI key**: Paste your key. The integration sends it as `X-API-Key`.
3. **Pick cameras.** Open the integration → Configure → select your cameras. On the next page, for each camera, select when it is scanned:
   - **On motion**: a scan runs when the selected binary sensor (motion, occupancy or presence) changes from off to on. This is the default when the camera's device has a motion sensor.
   - **Every N seconds** (minimum 5): a scan runs on a timer. This is the default (30 s) when no motion sensor is found.
   - **Off**: no auto-scan; use `image_processing.scan` or the service yourself.

You do not need an automation. **Each scan uses one API call.** Scans run only while the camera entity is available. The **cooldown** (default 10 s) is the minimum time between two scans of one camera. Inside the cooldown, the same plate on the same camera fires no new event.

Other options: regions (comma separated), minimum score, watched plates, endpoint URL, Frigate bridge.

## Entities
| Entity | State | Attributes |
|---|---|---|
| `image_processing.nakordoni_plate_<camera>` | number of plates | `vehicles[]{plate,confidence,region_code,vehicle_type}`, `plates`, `results` (full API fields), `processing_time`, `last_detection`, `camera_entity` |
| `sensor.nakordoni_last_plate_<camera>` | last plate | `confidence`, `region`, `box`, `candidates` |
| `binary_sensor.nakordoni_watched_<plate>` | `on` for 60 s after a match (strict or loose, see Gate opening) | `mode`, `camera_entity`, `confidence`, `last_seen` |
| `switch.nakordoni_gate_automation_enabled_<gate>` | `on` = the gate opens on a strict match | `gate_entity` |

The image processing entities do not poll: auto-scan runs them (see Quick start), or call `image_processing.scan`. Camera snapshots are always uploaded as bytes, because the API refuses private or LAN URLs.

## Gate opening
Version 0.4.0 can open a gate, a garage door or a barrier when a watched plate arrives. No automation is necessary.

### Strict watched-plate mode
New installs use **strict** mode. A watched plate matches only when all of these are true:
1. Only the **top reading** counts. The integration never uses `candidates`.
2. The plate is the same after normalisation (upper case, no spaces or dashes): `AA-1234 BB` = `AA1234BB`.
3. The score is at or above the strict minimum score (default **0.90**).
4. **N matching reads in a row** (default **2**) on one camera occur inside the window (default **15 s**). A read of a different plate, a low score or no plate starts the count again.

The old **loose** mode is still available. In loose mode, any reading or candidate at or above the minimum score turns the sensor on. Loose mode is for notifications only, not for gates. Installs from before 0.4.0 keep loose mode until you change it in Options. To set loose mode for some plates only, put them in "Plates in notify-only mode".

### Set up a gate
1. Open **Settings > Devices & services > Nakordoni Plate Recognition > Configure**.
2. Add the plates to "Watched plates", keep "Watched-plate mode" on **Strict**, and select "Set up gate opening".
3. On the gate page, set:
   - **Plates**: the plates that open this gate.
   - **Gate entity**: a `cover` (`cover.open_cover`), `switch` (`switch.turn_on`), `button` (`button.press`) or `lock` (`lock.unlock`).
   - **Allowed cameras** (optional): if empty, all cameras are allowed.
   - **Allowed from / until** (optional): a time window. A start later than the end means a window over midnight.
   - **Re-open cooldown**: default 60 s.
4. On each camera page, keep **Fast mode** on for gate cameras. Fast mode is on by default for cameras that feed a gate. It sends `config: {"mode": "fast"}` to the API, which skips the vehicle detector (about 0.1-0.2 s faster). The integration also sends `camera_id` and `timestamp`. If an older API version rejects these fields, the integration sends the request again without them.

A gate opens only on a strict match. After the first matching read on a gate camera, the integration scans that camera again after 1 s, so motion mode can also reach N reads. Each scan uses one API call.

When the gate opens, the integration fires the event `nakordoni_plate_gate_opened` (`plate`, `camera`, `gate`, `score`) and writes a logbook entry. To stop gate opening quickly, turn off the switch "gate automation enabled" of that gate.

> **Safety notes**
> - Use **strict** mode for gates. Loose mode is for notifications only.
> - Recognition uses a cloud API. If the internet or the API is down, the gate does not open. Keep a manual or remote way to open the gate (remote control, keypad, key).
> - Never make plate recognition the only access method for emergency services.
> - A plate can be copied. Do not use plate recognition as the only protection for high-security access.
> - Check the local rules about recording and processing number plates (for example GDPR in the EU) before you use this.

### Example: notify when the gate opens
```yaml
- alias: Gate opened by plate
  trigger: {platform: event, event_type: nakordoni_plate_gate_opened}
  action:
    service: notify.mobile_app_phone
    data: {message: "Gate {{ trigger.event.data.gate }} opened for {{ trigger.event.data.plate }} (score {{ trigger.event.data.score }})"}
```

## Event `nakordoni_plate_vehicle_detected`
`plate`, `confidence`, `region_code`, `vehicle_type`, `box`, `entity_id`, `camera_entity`. The integration fires one event for each plate that is at or above the minimum score. With the default endpoint (API v4), `region_code` is the ISO country of the plate (from the plate format) and `vehicle_type` is car, bus, truck or motorcycle; both are `null` when the API cannot tell. An endpoint URL on `/api/v2/` keeps returning `null` for both.

## Service `nakordoni_plate.recognize`
Give exactly one of these fields: `entity_id` (cameras), `image_url` (public URL) or `file_path` (must be in `allowlist_external_dirs`). The service returns the results as a response.

## Frigate bridge
To use the bridge, enable it in Options. The bridge requires the MQTT integration. It works as follows:
1. It listens on `frigate/events`.
2. On a `type: end` event for a vehicle label, it gets `/api/events/<id>/snapshot.jpg` and recognizes it.
3. It sends `POST /api/events/<id>/sub_label` with the best plate.

## Example automations (optional)
Auto-scan replaces the first example. Use it only when you set a camera to **Off** and want your own trigger.
```yaml
- alias: Scan driveway on motion
  trigger: {platform: state, entity_id: binary_sensor.driveway_motion, to: "on"}
  action: {service: image_processing.scan, target: {entity_id: image_processing.nakordoni_plate_driveway}}

- alias: Open gate for known car
  trigger: {platform: event, event_type: nakordoni_plate_vehicle_detected}
  condition: "{{ trigger.event.data.plate in ['AA1234BB','KR12345'] and trigger.event.data.confidence > 0.8 }}"
  action: {service: cover.open_cover, target: {entity_id: cover.gate}}

- alias: Notify when watched car arrives
  trigger: {platform: state, entity_id: binary_sensor.nakordoni_watched_aa1234bb, to: "on"}
  action: {service: notify.mobile_app_phone, data: {message: "AA1234BB seen at {{ trigger.to_state.attributes.camera_entity }}"}}

- alias: Recognize an uploaded file
  trigger: {platform: event, event_type: folder_watcher}
  action:
    - service: nakordoni_plate.recognize
      data: {file_path: "{{ trigger.event.data.path }}"}
      response_variable: res
    - service: persistent_notification.create
      data: {message: "{{ res }}"}
```

## Development
```
uv venv -p 3.13 .venv && uv pip install -p .venv pytest-homeassistant-custom-component PyTurboJPEG
.venv/bin/python -m pytest -q
```
