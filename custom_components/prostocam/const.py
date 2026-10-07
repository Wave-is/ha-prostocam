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

# Server error codes the integration tells apart.
ERROR_ALREADY_CONNECTED: Final = "already_connected"
ERROR_CLIENT_OUTDATED: Final = "client_outdated"

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
RETRY_MIN: Final = 2  # seconds
RETRY_MAX: Final = 60  # seconds
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 10  # seconds
MAX_USER_LENGTH: Final = 16
