"""Errors of ProstoCAM for the person who called an action or pressed a button.

A refusal of the server is not a failure of Home Assistant: it becomes a
`ServiceValidationError` with a translated phrase (Home Assistant answers it
with 400 and writes no trace to its log). Only a server that can not be
reached is a `HomeAssistantError`.

Every code the server may answer has its own phrase here (contract §6, §11,
§13). An unknown code gets the phrase of the server when it sent one (in the
language of Home Assistant, `Accept-Language`) or a general phrase; the code
itself is counted in the diagnostics of the integration, never shown raw.
"""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import (
    ProstoCamAuthError,
    ProstoCamError,
    ProstoCamRateLimitedError,
    ProstoCamRejectedError,
    ProstoCamScopeError,
    ProstoCamUnavailableError,
)
from .const import DOMAIN, LOGGER

# Refusals of the server (HTTP 4xx / 503 with a code) → translation key.
REFUSAL_KEYS: dict[str, str] = {
    "ai_credits_insufficient": "ai_credits_insufficient",
    "rate_limited": "rate_limited",
    "camera_not_found": "camera_not_found",
    "foreign_camera": "camera_not_found",
    "event_not_found": "event_not_found",
    "export_not_found": "export_not_found",
    "account_inactive": "account_inactive",
    "arming_readiness_unknown": "arming_readiness_unknown",
    "idempotency_conflict": "already_in_progress",
    "billing_unavailable": "server_busy",
    "no_recording": "no_recording",
    "not_found": "bridge_disabled",
    "home_assistant_disabled": "bridge_disabled",
}

# Refusals whose phrase names the refused field (the server message).
INVALID_CODES = frozenset({"invalid_body", "bad_request", "bad_idempotency_key"})

# `error_code` of a failed export (the codes of the export worker) → translation key.
EXPORT_KEYS: dict[str, str] = {
    "multipart.plan_empty": "export_no_recording",
    "no_recording": "export_no_recording",
    "gaps_rejected": "export_gaps",
    "preparation.topology": "export_gaps",
    "preparation.source": "export_recording_gone",
    "media.source_probe": "export_damaged",
    "media.decode": "export_damaged",
    "media.source_compatibility": "export_encoding_changed",
    "bundle.cancelled": "export_cancelled",
    "worker.invariant": "export_cannot_assemble",
    "media.stream_copy": "export_retry",
    "media.stream_copy_output": "export_retry",
    "media.exact_transcode": "export_retry",
    "media.exact_output": "export_retry",
    "media.publish": "export_retry",
    "preparation.manifest": "export_retry",
    "preparation.staging": "export_retry",
    "multipart.plan_invalid": "export_retry",
    "multipart_part.store_conflict": "export_retry",
    "multipart_part.store_unavailable": "export_retry",
    "multipart_part.store_lease_rejected": "export_retry",
    "multipart_part.load_conflict": "export_retry",
    "multipart_part.load_unavailable": "export_retry",
    "multipart_part.load_lease_rejected": "export_retry",
    "multipart_part.reuse_unavailable": "export_retry",
    "bundle.process": "export_retry",
    "bundle.lease": "export_retry",
    "bundle.protocol": "export_retry",
    "bundle.publication": "export_retry",
    "worker.abandoned": "export_retry",
}

# The codes of a failed export that mean "nothing was recorded at that time".
NO_RECORDING_CODES = frozenset(
    code for code, key in EXPORT_KEYS.items() if key == "export_no_recording"
)


def count_code(codes: dict[str, int] | None, code: str | None) -> None:
    """Count an error code for diagnostics (codes only, never a message)."""
    if codes is None:
        return
    name = code or "none"
    codes[name] = codes.get(name, 0) + 1


def command_error(
    err: ProstoCamError, codes: dict[str, int] | None = None
) -> HomeAssistantError:
    """An error of a command for the person who called it, in the language of HA."""
    code = getattr(err, "code", None)
    if isinstance(err, ProstoCamScopeError):
        count_code(codes, code)
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="scope_missing",
            translation_placeholders={"message": err.message or str(code)},
        )
    if isinstance(err, ProstoCamAuthError):
        count_code(codes, "token_invalid")
        return ServiceValidationError(
            translation_domain=DOMAIN, translation_key="token_invalid"
        )
    if isinstance(err, ProstoCamRateLimitedError):
        count_code(codes, "rate_limited")
        return ServiceValidationError(
            translation_domain=DOMAIN, translation_key="rate_limited"
        )
    if isinstance(err, ProstoCamUnavailableError):
        count_code(codes, code)
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=REFUSAL_KEYS.get(str(code), "server_busy"),
        )
    if isinstance(err, ProstoCamRejectedError):
        count_code(codes, code)
        if code in REFUSAL_KEYS:
            return ServiceValidationError(
                translation_domain=DOMAIN, translation_key=REFUSAL_KEYS[str(code)]
            )
        if code in INVALID_CODES:
            return ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_request",
                translation_placeholders={
                    "message": err.message or err.field or str(code)
                },
            )
        LOGGER.debug("ProstoCAM refusal with an unknown code %s", code)
        if err.message:
            # The phrase of the server is already in the language of HA.
            return ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="refused",
                translation_placeholders={"message": err.message},
            )
        return ServiceValidationError(
            translation_domain=DOMAIN, translation_key="refused_unknown"
        )
    count_code(codes, code or type(err).__name__)
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="request_failed",
        translation_placeholders={"error": str(err)},
    )


def export_error(
    code: str | None,
    codes: dict[str, int] | None = None,
    nearest: str | None = None,
) -> ServiceValidationError:
    """A failed export: the reason in plain words (the code goes to diagnostics)."""
    count_code(codes, code)
    key = EXPORT_KEYS.get(code or "")
    if key is None:
        LOGGER.debug("ProstoCAM export failed with an unknown code %s", code)
        key = "export_failed"
    if key == "export_no_recording" and nearest:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="export_no_recording_nearest",
            translation_placeholders={"nearest": nearest},
        )
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def recorded_ranges(detail: dict[str, Any]) -> list[tuple[int, int]]:
    """`recorded_before` / `recorded_after` of a `no_recording` refusal: (start, seconds)."""
    ranges: list[tuple[int, int]] = []
    for key in ("recorded_before", "recorded_after"):
        part = detail.get(key)
        if not isinstance(part, dict):
            continue
        start = part.get("from")
        duration = part.get("duration")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(duration, int)
            and not isinstance(duration, bool)
            and duration >= 0
        ):
            ranges.append((start, duration))
    return ranges
