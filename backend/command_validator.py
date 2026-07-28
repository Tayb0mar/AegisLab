"""Validation gate for hardware commands (SR-003, FR-080..FR-084).

Nothing reaches the Arduino unless it passes this module. The policy for
unknown keys is REJECT: a proposal containing keys outside the documented
schema fails validation. Free-form LLM text can therefore never be forwarded;
only the canonical, re-serialised form produced by :func:`to_serial_line` is
ever written to the serial port.

Schema (all commands):
    action            required, one of the allowlist
    reason            required, 1..200 printable chars, audit-only, never sent
    duration_seconds  required for 'activate_warning', integer 1..10
    message           required for 'display_message', 1..16 printable ASCII chars
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ALLOWED_ACTIONS = (
    "activate_warning",
    "deactivate_warning",
    "display_message",
    "request_status",
)

MIN_DURATION_SECONDS = 1
MAX_DURATION_SECONDS = 10
MAX_MESSAGE_LENGTH = 16  # one 16x2 LCD line
MAX_REASON_LENGTH = 200

_KEYS_BY_ACTION: dict[str, set[str]] = {
    "activate_warning": {"action", "reason", "duration_seconds"},
    "deactivate_warning": {"action", "reason"},
    "display_message": {"action", "reason", "message"},
    "request_status": {"action", "reason"},
}


class CommandValidationError(Exception):
    """A command proposal was rejected before serial transmission."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedCommand:
    """Canonical command produced exclusively by :func:`validate_command`."""

    action: str
    reason: str
    duration_seconds: int | None = None
    message: str | None = None


def _require_printable(value: str, name: str) -> None:
    if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in value):
        raise CommandValidationError(
            "NON_PRINTABLE", f"{name} must contain printable ASCII only"
        )


def validate_command(proposal: Any) -> ValidatedCommand:
    """Validate an untrusted command proposal (e.g. from an API or an LLM)."""
    if not isinstance(proposal, dict):
        raise CommandValidationError("NOT_AN_OBJECT", "command must be a JSON object")

    action = proposal.get("action")
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        raise CommandValidationError(
            "UNKNOWN_ACTION",
            f"action must be one of {', '.join(ALLOWED_ACTIONS)}",
        )

    allowed_keys = _KEYS_BY_ACTION[action]
    extra_keys = set(proposal.keys()) - allowed_keys
    if extra_keys:
        raise CommandValidationError(
            "UNEXPECTED_KEY",
            f"unexpected keys for {action}: {', '.join(sorted(extra_keys))}",
        )

    reason = proposal.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise CommandValidationError("MISSING_REASON", "reason is required")
    if len(reason) > MAX_REASON_LENGTH:
        raise CommandValidationError(
            "REASON_TOO_LONG", f"reason must be <= {MAX_REASON_LENGTH} characters"
        )

    duration: int | None = None
    if action == "activate_warning":
        raw_duration = proposal.get("duration_seconds")
        if isinstance(raw_duration, bool) or not isinstance(raw_duration, int):
            raise CommandValidationError(
                "INVALID_DURATION", "duration_seconds must be an integer"
            )
        if not MIN_DURATION_SECONDS <= raw_duration <= MAX_DURATION_SECONDS:
            raise CommandValidationError(
                "DURATION_OUT_OF_RANGE",
                f"duration_seconds must be between {MIN_DURATION_SECONDS} "
                f"and {MAX_DURATION_SECONDS}",
            )
        duration = raw_duration

    text: str | None = None
    if action == "display_message":
        raw_message = proposal.get("message")
        if not isinstance(raw_message, str) or not raw_message.strip():
            raise CommandValidationError("MISSING_MESSAGE", "message is required")
        if len(raw_message) > MAX_MESSAGE_LENGTH:
            raise CommandValidationError(
                "MESSAGE_TOO_LONG",
                f"message must be <= {MAX_MESSAGE_LENGTH} characters",
            )
        _require_printable(raw_message, "message")
        text = raw_message

    return ValidatedCommand(
        action=action, reason=reason.strip(), duration_seconds=duration, message=text
    )


def to_serial_line(command: ValidatedCommand) -> bytes:
    """Serialise a validated command for the firmware.

    The ``reason`` is intentionally excluded: it exists for auditing on the
    backend side and is never transmitted to the device.
    """
    payload: dict[str, Any] = {"action": command.action}
    if command.duration_seconds is not None:
        payload["duration_seconds"] = command.duration_seconds
    if command.message is not None:
        payload["message"] = command.message
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("ascii")
