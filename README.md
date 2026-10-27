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
| `binary_sensor.nakordoni_watched_<plate>` | `on` for 60 s after a match (plate or candidate) | `camera_entity`, `confidence`, `last_seen` |

The image processing entities do not poll: auto-scan runs them (see Quick start), or call `image_processing.scan`. Camera snapshots are always uploaded as bytes, because the API refuses private or LAN URLs.

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
