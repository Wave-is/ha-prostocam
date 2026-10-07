"""Constants for the ProstoCAM integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "prostocam"
LOGGER = logging.getLogger(__package__)

# Keep in sync with manifest.json.
VERSION: Final = "0.2.0"

DEFAULT_SERVER: Final = "https://new.prosto.cam"
DEFAULT_TITLE: Final = "ProstoCAM"

# ProstoCAM server API (JSON over HTTPS, `Authorization: Bearer <token>`).
API_PREFIX: Final = "/v2/smart-home/ha"
PATH_PAIR: Final = "/pair"
PATH_ENTITIES: Final = "/entities"
PATH_EVENTS: Final = "/events"
PATH_ALARM: Final = "/alarm"
PATH_HEARTBEAT: Final = "/heartbeat"

# Keys of server replies. Every reply (and the pairing reply) carries `config`.
KEY_TOKEN: Final = "token"
KEY_INTEGRATION_ID: Final = "integration_id"
KEY_DISPLAY_NAME: Final = "display_name"
KEY_CONFIG: Final = "config"
KEY_ENABLED: Final = "enabled_entities"
KEY_HEARTBEAT_INTERVAL: Final = "heartbeat_interval_s"
KEY_CATALOG_DOMAINS: Final = "catalog_domains"
KEY_EVENT_DOMAINS: Final = "event_domains"
KEY_MAX_BATCH: Final = "max_events_per_batch"
KEY_MAX_CATALOG: Final = "max_catalog_entities"
KEY_LATE_AFTER: Final = "late_after_s"
# Proposed to the server (not in protocol 1 yet): `true` = catalog items may carry `battery`.
KEY_BATTERY_LEVELS: Final = "battery_levels"
BATTERY_ATTRIBUTES: Final = ("battery_level", "battery")
KEY_PROTOCOL_VERSION: Final = "protocol_version"
KEY_RESULTS: Final = "results"
PROTOCOL_VERSION: Final = 2

# Server error codes the integration tells apart.
ERROR_ALREADY_CONNECTED: Final = "already_connected"
ERROR_CLIENT_OUTDATED: Final = "client_outdated"
ERROR_NOT_FOUND: Final = "not_found"

# Config entry data.
CONF_CODE: Final = "code"
CONF_SERVER: Final = "server"
CONF_TOKEN: Final = "token"
CONF_INTEGRATION_ID: Final = "integration_id"

# Options.
CONF_DOMAINS: Final = "domains"
CONF_DEVICE_CLASSES: Final = "device_classes"

ALARM_DOMAIN: Final = "alarm_control_panel"
SUPPORTED_DOMAINS: Final = ["binary_sensor", ALARM_DOMAIN, "siren"]
DEFAULT_DOMAINS: Final = ["binary_sensor", ALARM_DOMAIN, "siren"]

# Binary sensor device classes that matter for security; others are never sent.
SUPPORTED_DEVICE_CLASSES: Final = [
    "door",
    "garage_door",
    "window",
    "opening",
    "motion",
    "occupancy",
    "presence",
    "moisture",
    "smoke",
    "gas",
    "carbon_monoxide",
    "heat",
    "safety",
    "tamper",
    "vibration",
    "sound",
    "problem",
]
DEFAULT_DEVICE_CLASSES: Final = [
    "door",
    "garage_door",
    "window",
    "opening",
    "motion",
    "occupancy",
    "presence",
    "moisture",
    "smoke",
    "gas",
    "carbon_monoxide",
    "safety",
    "tamper",
    "vibration",
]

# Transitions from/to these states are not events (restarts, lost radio links).
SKIP_STATES: Final = frozenset({"unavailable", "unknown"})

ISSUE_OUTDATED: Final = "client_outdated"

CODE_LENGTH: Final = 8
REQUEST_TIMEOUT: Final = 15  # seconds
HEARTBEAT_INTERVAL: Final = 60  # seconds, the server may change it
HEARTBEAT_MIN: Final = 30
HEARTBEAT_MAX: Final = 3600
# The server takes the whole catalog at most 30 times an hour.
CATALOG_COOLDOWN: Final = 150  # seconds
BATCH_SIZE: Final = 100
MAX_CATALOG: Final = 2000
MAX_QUEUE: Final = 1000
RETRY_MIN: Final = 5  # seconds
RETRY_MAX: Final = 300  # seconds
RATE_LIMIT_PAUSE: Final = 60  # seconds; the server sends no Retry-After
DISABLED_RECHECK: Final = 600  # seconds between heartbeats while the bridge is off
LATE_AFTER: Final = 120  # seconds, the server may change it
MAX_QUEUED_S: Final = 86400
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 10  # seconds
MAX_USER_LENGTH: Final = 16

# ---------------------------------------------------------------- protocol 2
# Cameras of ProstoCAM in Home Assistant (contract §8–§9).
KEY_CAPABILITIES: Final = "capabilities"
SCOPE_CAMERAS: Final = "cameras:read"
SCOPE_EVENTS: Final = "events:read"
SCOPE_LIVE: Final = "live:read"
CAMERA_SCOPES: Final = (SCOPE_CAMERAS, SCOPE_EVENTS, SCOPE_LIVE)
CAMERAS_PROTOCOL: Final = 2

PATH_CAMERAS: Final = "/cameras"
PATH_STREAM: Final = "/v2/stream"  # from the server root, not under API_PREFIX

ERROR_SCOPE_MISSING: Final = "scope_missing"
ERROR_STREAM_SCOPE_MISSING: Final = "stream_scope_missing"
SCOPE_ERRORS: Final = frozenset({ERROR_SCOPE_MISSING, ERROR_STREAM_SCOPE_MISSING})

ISSUE_MISSING_ACCESS: Final = "missing_access"

CATALOG_INTERVAL: Final = 300  # seconds; the server asks for at most one in 5 minutes
SNAPSHOT_CACHE: Final = 10  # seconds
LIVE_URL_MAX_AGE: Final = 240  # seconds; the start token lives 300 s
STREAM_REFRESH_MIN: Final = 30  # seconds between two fresh addresses for a broken stream
DETECTION_RESET: Final = 30  # seconds a motion/person/vehicle sensor stays on
SSE_READ_TIMEOUT: Final = 45  # seconds without a byte (pulse every 15 s) = reconnect
SSE_RETRY_MIN: Final = 3  # seconds
SSE_RETRY_MAX: Final = 300  # seconds
SSE_SAVE_DELAY: Final = 30  # seconds

EVENT_TYPES: Final = ["motion", "person", "vehicle", "animal", "other", "test"]
# Alarm classes of ProstoCAM that are not an event type of their own.
EVENT_TYPE_OF_CLASS: Final = {
    "motion": "motion",
    "person": "person",
    "vehicle": "vehicle",
    "animal": "animal",
}
DETECTIONS: Final = ("motion", "person", "vehicle")

PLATFORMS: Final = ["camera", "binary_sensor", "event", "image"]
