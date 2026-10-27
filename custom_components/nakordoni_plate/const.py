"""Constants for Nakordoni Plate Recognition."""
from __future__ import annotations

DOMAIN = "nakordoni_plate"

DEFAULT_API_URL = "https://nakordoni.eu/api/v4/data/plate-recognition"
OAUTH2_AUTHORIZE = "https://api.nakordoni.dev/oauth/authorize"
OAUTH2_TOKEN = "https://api.nakordoni.dev/oauth/token"
# First-party PUBLIC client (PKCE, no secret checked by the server). Client #13, F2187.
BUNDLED_CLIENT_ID = "nkd_3f595417301a83ff4ed4dd47"
BUNDLED_CLIENT_SECRET = "public-pkce-client"
BUNDLED_AUTH_DOMAIN = "nakordoni_first_party"

CONF_AUTH_TYPE = "auth_type"
AUTH_OAUTH = "oauth"
AUTH_API_KEY = "api_key"

CONF_API_URL = "api_url"
CONF_REGIONS = "regions"
CONF_MIN_SCORE = "min_score"
CONF_WATCHED_PLATES = "watched_plates"
CONF_CAMERAS = "cameras"
CONF_FRIGATE_ENABLED = "frigate_enabled"
CONF_FRIGATE_URL = "frigate_url"
CONF_FRIGATE_TOPIC = "frigate_topic"
CONF_FRIGATE_LABELS = "frigate_labels"

DEFAULT_MIN_SCORE = 0.6
DEFAULT_FRIGATE_URL = "http://ccab4aaf-frigate:5000"
DEFAULT_FRIGATE_TOPIC = "frigate/events"
DEFAULT_FRIGATE_LABELS = "car,motorcycle,bus,truck"
WATCHED_ON_SECONDS = 60

EVENT_VEHICLE_DETECTED = f"{DOMAIN}_vehicle_detected"
SIGNAL_RESULT = f"{DOMAIN}_result_{{}}"  # format with entry_id

SERVICE_RECOGNIZE = "recognize"
ATTR_IMAGE_URL = "image_url"
ATTR_FILE_PATH = "file_path"
ATTR_CAMERA_ENTITY = "camera_entity"

MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Auto-scan (v0.3.0): per-camera scan mode, so no automation is needed.
CONF_CAMERA_SCAN = "camera_scan"  # {camera_entity: {scan_mode, motion_entity, scan_interval}}
CONF_SCAN_MODE = "scan_mode"
CONF_MOTION_ENTITY = "motion_entity"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_COOLDOWN = "cooldown"
SCAN_OFF = "off"
SCAN_MOTION = "motion"
SCAN_INTERVAL = "interval"
SCAN_MODES = [SCAN_OFF, SCAN_MOTION, SCAN_INTERVAL]
MIN_SCAN_INTERVAL = 5
DEFAULT_SCAN_INTERVAL = 10  # form default when the user picks "every N seconds"
DEFAULT_AUTO_INTERVAL = 30  # default for a new camera without a motion sensor
DEFAULT_COOLDOWN = 10
MOTION_DEVICE_CLASSES = ("motion", "occupancy", "presence")
