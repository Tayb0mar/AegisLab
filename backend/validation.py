"""Validation of serial messages at the trust boundary.

Everything arriving from the serial port (or the simulator) is untrusted text.
This module turns a raw line into either a typed, range-checked structure or a
:class:`ValidationError` carrying a machine-readable reason. It never raises
anything else for bad input and never crashes the collector.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from backend.config import Settings

PROTOCOL_VERSION = 1

READING_REQUIRED_KEYS = ("temperature", "humidity", "light", "motion")


class MessageType(str, Enum):
    READING = "reading"
    SENSOR_ERROR = "sensor_error"
    COMMAND_ACK = "command_ack"
    COMMAND_REJECTED = "command_rejected"
    DEVICE_STATUS = "device_status"
    UNKNOWN = "unknown"


class ValidationError(Exception):
    """A message failed validation. ``code`` is stable and machine readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedReading:
    """A reading that passed schema and physical-range validation."""

    temperature: float | None
    humidity: float | None
    light: int | None
    motion: bool
    simulated: bool = False
    uptime_ms: int | None = None


@dataclass(frozen=True)
class SensorErrorEvent:
    """A device-reported sensor failure."""

    sensor: str
    code: str


def parse_line(raw: bytes | str) -> dict[str, Any]:
    """Decode and JSON-parse one serial line. Raises ValidationError on failure."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("DECODE_FAILED", "line is not valid UTF-8") from exc
    else:
        text = raw

    text = text.strip()
    if not text:
        raise ValidationError("EMPTY_LINE", "line is empty")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError("INVALID_JSON", f"not valid JSON: {text[:120]!r}") from exc

    if not isinstance(parsed, dict):
        raise ValidationError("NOT_AN_OBJECT", "JSON root must be an object")
    return parsed


def classify_message(message: dict[str, Any]) -> MessageType:
    """Decide what kind of message this is before validating its content."""
    status = message.get("status")
    if status == "sensor_error":
        return MessageType.SENSOR_ERROR
    if status == "command_ack":
        return MessageType.COMMAND_ACK
    if status == "command_rejected":
        return MessageType.COMMAND_REJECTED
    if status == "device_status":
        return MessageType.DEVICE_STATUS
    if all(key in message for key in READING_REQUIRED_KEYS):
        return MessageType.READING
    return MessageType.UNKNOWN


def _numeric_or_none(
    message: dict[str, Any], key: str, minimum: float, maximum: float
) -> float | None:
    """Extract an optional finite number within [minimum, maximum].

    Booleans are explicitly rejected: in Python ``bool`` is a subclass of
    ``int``, so ``isinstance(True, (int, float))`` would silently accept it.
    """
    value = message[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("WRONG_TYPE", f"{key} must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError("NOT_FINITE", f"{key} must be finite")
    if number < minimum or number > maximum:
        raise ValidationError(
            "OUT_OF_RANGE",
            f"{key}={number} outside allowed range [{minimum}, {maximum}]",
        )
    return number


def validate_reading(message: dict[str, Any], settings: Settings) -> ValidatedReading:
    """Validate schema, types and physical ranges of a reading message.

    Unknown extra keys are ignored (documented policy for readings: the
    firmware may add fields such as ``v`` or ``uptime_ms`` without breaking
    older collectors). Missing required keys are rejected.
    """
    for key in READING_REQUIRED_KEYS:
        if key not in message:
            raise ValidationError("MISSING_FIELD", f"missing required field {key!r}")

    temperature = _numeric_or_none(
        message, "temperature", settings.temperature_min_c, settings.temperature_max_c
    )
    humidity = _numeric_or_none(message, "humidity", 0.0, 100.0)

    light_raw = _numeric_or_none(
        message, "light", float(settings.light_min), float(settings.light_max)
    )
    if light_raw is not None and not float(light_raw).is_integer():
        raise ValidationError("WRONG_TYPE", "light must be an integer")
    light = int(light_raw) if light_raw is not None else None

    motion = message["motion"]
    if not isinstance(motion, bool):
        raise ValidationError("WRONG_TYPE", "motion must be a JSON boolean")

    simulated = message.get("simulated")
    if simulated is None:
        simulated = False
    if not isinstance(simulated, bool):
        raise ValidationError("WRONG_TYPE", "simulated must be a JSON boolean")

    uptime_ms = message.get("uptime_ms")
    if uptime_ms is not None:
        if isinstance(uptime_ms, bool) or not isinstance(uptime_ms, int):
            raise ValidationError("WRONG_TYPE", "uptime_ms must be an integer")
        if uptime_ms < 0:
            raise ValidationError("OUT_OF_RANGE", "uptime_ms must be >= 0")

    return ValidatedReading(
        temperature=temperature,
        humidity=humidity,
        light=light,
        motion=motion,
        simulated=simulated,
        uptime_ms=uptime_ms,
    )


def validate_sensor_error(message: dict[str, Any]) -> SensorErrorEvent:
    """Validate a device-reported sensor error message."""
    sensor = message.get("sensor")
    code = message.get("code")
    if not isinstance(sensor, str) or not sensor:
        raise ValidationError("MISSING_FIELD", "sensor_error requires 'sensor'")
    if not isinstance(code, str) or not code:
        raise ValidationError("MISSING_FIELD", "sensor_error requires 'code'")
    return SensorErrorEvent(sensor=sensor[:50], code=code[:50])
