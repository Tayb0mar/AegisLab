"""Tests for serial-boundary validation (parsing, schema, ranges)."""

from __future__ import annotations

import json

import pytest

from backend.validation import (
    MessageType,
    ValidationError,
    classify_message,
    parse_line,
    validate_reading,
    validate_sensor_error,
)

VALID = {"temperature": 24.6, "humidity": 51.2, "light": 430, "motion": False}


def line(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


# ------------------------------------------------------------------ parsing


def test_parse_valid_line() -> None:
    assert parse_line(line(VALID)) == VALID


def test_parse_rejects_malformed_json() -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_line(b'{"temperature": 24.6,\n')
    assert excinfo.value.code == "INVALID_JSON"


def test_parse_rejects_empty_line() -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_line(b"   \n")
    assert excinfo.value.code == "EMPTY_LINE"


def test_parse_rejects_non_utf8() -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_line(b"\xff\xfe{}\n")
    assert excinfo.value.code == "DECODE_FAILED"


def test_parse_rejects_non_object_root() -> None:
    with pytest.raises(ValidationError) as excinfo:
        parse_line(b"[1, 2, 3]\n")
    assert excinfo.value.code == "NOT_AN_OBJECT"


# ------------------------------------------------------------ classification


def test_classify_reading() -> None:
    assert classify_message(VALID) == MessageType.READING


def test_classify_sensor_error() -> None:
    message = {"status": "sensor_error", "sensor": "dht", "code": "READ_FAILED"}
    assert classify_message(message) == MessageType.SENSOR_ERROR


def test_classify_unknown() -> None:
    assert classify_message({"foo": 1}) == MessageType.UNKNOWN


# ------------------------------------------------------------------ readings


def test_valid_reading_accepted(settings) -> None:
    reading = validate_reading(VALID, settings)
    assert reading.temperature == pytest.approx(24.6)
    assert reading.humidity == pytest.approx(51.2)
    assert reading.light == 430
    assert reading.motion is False
    assert reading.simulated is False


def test_reading_with_extra_keys_accepted(settings) -> None:
    message = {**VALID, "v": 1, "uptime_ms": 5000, "status": "ok"}
    reading = validate_reading(message, settings)
    assert reading.uptime_ms == 5000


def test_simulated_flag_preserved(settings) -> None:
    reading = validate_reading({**VALID, "simulated": True}, settings)
    assert reading.simulated is True


def test_missing_field_rejected(settings) -> None:
    for key in ("temperature", "humidity", "light", "motion"):
        broken = {k: v for k, v in VALID.items() if k != key}
        with pytest.raises(ValidationError) as excinfo:
            validate_reading(broken, settings)
        assert excinfo.value.code == "MISSING_FIELD"


def test_boolean_as_number_rejected(settings) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_reading({**VALID, "temperature": True}, settings)
    assert excinfo.value.code == "WRONG_TYPE"


def test_numeric_motion_rejected(settings) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_reading({**VALID, "motion": 1}, settings)
    assert excinfo.value.code == "WRONG_TYPE"


def test_non_finite_rejected(settings) -> None:
    # json.loads accepts bare NaN/Infinity, so they must be caught here.
    message = json.loads('{"temperature": NaN, "humidity": 50, "light": 1, "motion": true}')
    with pytest.raises(ValidationError) as excinfo:
        validate_reading(message, settings)
    assert excinfo.value.code == "NOT_FINITE"


def test_humidity_above_100_rejected(settings) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_reading({**VALID, "humidity": 100.1}, settings)
    assert excinfo.value.code == "OUT_OF_RANGE"


def test_temperature_out_of_range_rejected(settings) -> None:
    with pytest.raises(ValidationError):
        validate_reading({**VALID, "temperature": -100}, settings)
    with pytest.raises(ValidationError):
        validate_reading({**VALID, "temperature": 200}, settings)


def test_light_out_of_range_rejected(settings) -> None:
    with pytest.raises(ValidationError):
        validate_reading({**VALID, "light": 1024}, settings)
    with pytest.raises(ValidationError):
        validate_reading({**VALID, "light": -1}, settings)


def test_light_must_be_integer(settings) -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_reading({**VALID, "light": 430.5}, settings)
    assert excinfo.value.code == "WRONG_TYPE"


def test_null_sensor_values_allowed(settings) -> None:
    reading = validate_reading(
        {"temperature": None, "humidity": None, "light": None, "motion": True},
        settings,
    )
    assert reading.temperature is None
    assert reading.humidity is None
    assert reading.light is None
    assert reading.motion is True


def test_boundary_values_accepted(settings) -> None:
    reading = validate_reading(
        {"temperature": -40, "humidity": 0, "light": 0, "motion": False}, settings
    )
    assert reading.temperature == -40
    reading = validate_reading(
        {"temperature": 85, "humidity": 100, "light": 1023, "motion": True}, settings
    )
    assert reading.light == 1023


# -------------------------------------------------------------- sensor error


def test_sensor_error_valid() -> None:
    event = validate_sensor_error(
        {"status": "sensor_error", "sensor": "dht", "code": "READ_FAILED"}
    )
    assert event.sensor == "dht"
    assert event.code == "READ_FAILED"


def test_sensor_error_missing_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_sensor_error({"status": "sensor_error", "sensor": "dht"})
    with pytest.raises(ValidationError):
        validate_sensor_error({"status": "sensor_error", "code": "X"})
