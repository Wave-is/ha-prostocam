"""Constants for the ProstoCAM integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "prostocam"
LOGGER = logging.getLogger(__package__)

# Keep in sync with manifest.json.
VERSION: Final = "0.1.0"

DEFAULT_SERVER: Final = "https://new.prosto.cam"
DEFAULT_TITLE: Final = "ProstoCAM"

# ProstoCAM server API (JSON over HTTPS, `Authorization: Bearer <token>`).
API_PREFIX: Final = "/v2/smart-home/ha"
PATH_PAIR: Final = "/pair"
PATH_ENTITIES: Final = "/entities"
PATH_EVENTS: Final = "/events"
PATH_ALARM: Final = "/alarm"
PATH_HEARTBEAT: Final = "/heartbeat"

# Keys of server replies.
KEY_TOKEN: Final = "token"
KEY_INTEGRATION_ID: Final = "integration_id"
KEY_TITLE: Final = "title"
KEY_ENABLED: Final = "enabled_entities"
KEY_HEARTBEAT_INTERVAL: Final = "heartbeat_interval"

# Config entry data.
CONF_CODE: Final = "code"
CONF_SERVER: Final = "server"
CONF_TOKEN: Final = "token"
CONF_INTEGRATION_ID: Final = "integration_id"

# Options.
CONF_DOMAINS: Final = "domains"
CONF_DEVICE_CLASSES: Final = "device_classes"

SUPPORTED_DOMAINS: Final = [
    "binary_sensor",
    "alarm_control_panel",
    "lock",
    "siren",
    "cover",
    "event",
]
DEFAULT_DOMAINS: Final = ["binary_sensor", "alarm_control_panel"]

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

ALARM_DOMAIN: Final = "alarm_control_panel"

# Transitions from/to these states are not events (restarts, lost radio links).
SKIP_STATES: Final = frozenset({"unavailable", "unknown"})

CODE_LENGTH: Final = 8
REQUEST_TIMEOUT: Final = 15  # seconds
HEARTBEAT_INTERVAL: Final = 120  # seconds, the server may change it
HEARTBEAT_MIN: Final = 30
HEARTBEAT_MAX: Final = 3600
CATALOG_DEBOUNCE: Final = 15  # seconds
BATCH_SIZE: Final = 50
MAX_QUEUE: Final = 1000
RETRY_MIN: Final = 2  # seconds
RETRY_MAX: Final = 60  # seconds
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 10  # seconds
