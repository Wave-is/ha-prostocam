"""Constants for the ProstoCAM integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "prostocam"
LOGGER = logging.getLogger(__package__)

# Keep in sync with manifest.json.
VERSION: Final = "1.0.0"

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
PROTOCOL_VERSION: Final = 4

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

# ---------------------------------------------------------------- protocol 3
# Media, ProstoCAM arming, buttons and money (contract §11–§12). The areas are
# never given by default: the subscriber ticks each one in the web account.
CONTROL_PROTOCOL: Final = 3
SCOPE_ARCHIVE: Final = "archive:read"
SCOPE_ARMING: Final = "arming:write"
SCOPE_ACTIONS: Final = "actions:write"
SCOPE_ACCOUNT: Final = "account:read"
CONTROL_SCOPES: Final = (SCOPE_ARCHIVE, SCOPE_ARMING, SCOPE_ACTIONS, SCOPE_ACCOUNT)

PATH_ARMING: Final = "/arming"
PATH_ACCOUNT: Final = "/account"
PATH_EVENT: Final = "/events/{event_id}"  # + /snapshot, /clip, /ai-verify
PATH_CAMERA: Final = "/cameras/{camera_id}"  # + /events, /archive, /mute, ...

# Words of the ProstoCAM arming for Home Assistant (contract §11.4).
ARMING_STATES: Final = ("disarmed", "armed_home", "armed_night", "armed_away")
ARMING_POLICY_DEGRADED: Final = "degraded"
ERROR_ARMING_NOT_READY: Final = "arming_not_ready"
ERROR_UNCOVERED: Final = "arming_uncovered_not_acknowledged"
ERROR_VERSION_CONFLICT: Final = "version_conflict"
ERROR_ECHO_SUPPRESSED: Final = "arming_echo_suppressed"
ERROR_AI_CONFIRM: Final = "ai_credit_confirmation_required"
ERROR_FRAME_MISSING: Final = "event_frame_missing"
ERROR_CLIP_UNAVAILABLE: Final = "clip_unavailable"

# Options: two-way sync of the ProstoCAM arming with an alarm panel of Home Assistant.
CONF_ARMING_SYNC: Final = "arming_sync"
CONF_ARMING_SYNC_ENTITY: Final = "arming_sync_entity"
# A mode the other side set less than this ago is not mirrored back (contract §11.4: 15 s).
SYNC_ECHO_WINDOW: Final = 15  # seconds
# Modes of a Home Assistant panel that ProstoCAM knows; others are not mirrored.
HA_PANEL_TO_ARMING: Final = {
    "disarmed": "disarmed",
    "armed_home": "armed_home",
    "armed_night": "armed_night",
    "armed_away": "armed_away",
    "armed_vacation": "armed_away",
    "armed_custom_bypass": "armed_home",
}

ARMING_POLL: Final = 60  # seconds, only while the event channel is down
ARMING_REFRESH: Final = 900  # seconds, a safety read while the channel is up
TRIGGERED_HOLD: Final = 120  # seconds the panel shows "triggered" after an alarm
ACCOUNT_INTERVAL: Final = 900  # seconds; the server asks for at most one in 15 minutes
MUTE_INTERVAL: Final = 300  # seconds
DETERRENCE_INTERVAL: Final = 86400  # seconds
AI_CONFIRM_WINDOW: Final = 30  # seconds between the two presses of "Check with AI"
MORNING_HOUR: Final = 7  # "until the morning" = until 07:00 local time
MAX_MUTE_MINUTES: Final = 1440
EVENT_PAGE_LIMIT: Final = 100
EVENT_MAX_PAGES: Final = 10
MEDIA_DAYS: Final = 7
FRAME_CACHE_SIZE: Final = 32
FRAME_RETRY: Final = 3  # seconds before the second try of a frame not saved yet

ISSUE_ACCOUNT_STAGE: Final = "account_stage"
ACCOUNT_STAGES_TO_FIX: Final = frozenset({"restricted", "suspended"})

# Home Assistant events for automations and the notification blueprint.
EVENT_ALARM: Final = "prostocam_alarm"
EVENT_AI_VERDICT: Final = "prostocam_ai_verdict"

# Services.
SERVICE_MUTE: Final = "mute"
SERVICE_TEST_ALARM: Final = "test_alarm"
SERVICE_VERIFY_AI: Final = "verify_ai"
SERVICE_ARM_ANYWAY: Final = "arm_anyway"

# ---------------------------------------------------------------- protocol 4
# Own WebRTC, "ask the archive", "export a clip" (contract §13–§14). No new areas:
# WebRTC needs `live:read`, the question and the export `archive:read`.
ARCHIVE_PROTOCOL: Final = 4
WEBRTC_PROTOCOL: Final = 4
# A camera whose WebRTC failed shows HLS for a while (503 = until the next catalog).
WEBRTC_RETRY: Final = 3600  # seconds after a failed negotiation (codec, network)
ERROR_WEBRTC_UNAVAILABLE: Final = "webrtc_unavailable"
ERROR_WEBRTC_FAILED: Final = "webrtc_negotiation_failed"
ERROR_EXPORT_NOT_FOUND: Final = "export_not_found"

PATH_ASK: Final = "/ask"
ASK_MAX_LENGTH: Final = 300
ASK_DEFAULT_LIMIT: Final = 10
ASK_MAX_LIMIT: Final = 50
EXPORT_MAX_DURATION: Final = 600  # seconds
EXPORT_POLL: Final = 5  # seconds between two reads of a job
EXPORT_WAIT: Final = 300  # seconds the action waits for the file at most
EXPORTS_KEPT: Final = 20  # jobs per camera listed in Media

EVENT_EXPORT_READY: Final = "prostocam_export_ready"
SERVICE_ASK_ARCHIVE: Final = "ask_archive"
SERVICE_EXPORT_CLIP: Final = "export_clip"
INTENT_ASK_ARCHIVE: Final = "ProstoCamAskArchive"

# Frames of events reach the browser and the companion app through Home Assistant.
VIEW_EVENT_SNAPSHOT: Final = "/api/prostocam/{entry_id}/events/{event_id}/snapshot.jpg"

PLATFORMS: Final = [
    "camera",
    "binary_sensor",
    "event",
    "image",
    "alarm_control_panel",
    "button",
    "select",
    "sensor",
]
