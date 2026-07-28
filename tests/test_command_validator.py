"""Tests for the hardware-command validation gate."""

from __future__ import annotations

import json

import pytest

from backend.command_validator import (
    ALLOWED_ACTIONS,
    CommandValidationError,
    ValidatedCommand,
    to_serial_line,
    validate_command,
)


def test_all_allowlisted_actions_accepted() -> None:
    proposals = {
        "activate_warning": {
            "action": "activate_warning",
            "reason": "test",
            "duration_seconds": 3,
        },
        "deactivate_warning": {"action": "deactivate_warning", "reason": "test"},
        "display_message": {
            "action": "display_message",
            "reason": "test",
            "message": "Hello lab",
        },
        "request_status": {"action": "request_status", "reason": "test"},
    }
    assert set(proposals) == set(ALLOWED_ACTIONS)
    for action, proposal in proposals.items():
        command = validate_command(proposal)
        assert command.action == action


def test_unknown_action_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command({"action": "open_pod_bay_doors", "reason": "please"})
    assert excinfo.value.code == "UNKNOWN_ACTION"


def test_non_object_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command("activate_warning")
    assert excinfo.value.code == "NOT_AN_OBJECT"


def test_missing_reason_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command({"action": "request_status"})
    assert excinfo.value.code == "MISSING_REASON"


def test_excessive_duration_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command(
            {"action": "activate_warning", "reason": "t", "duration_seconds": 11}
        )
    assert excinfo.value.code == "DURATION_OUT_OF_RANGE"


def test_zero_duration_rejected() -> None:
    with pytest.raises(CommandValidationError):
        validate_command(
            {"action": "activate_warning", "reason": "t", "duration_seconds": 0}
        )


def test_boolean_duration_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command(
            {"action": "activate_warning", "reason": "t", "duration_seconds": True}
        )
    assert excinfo.value.code == "INVALID_DURATION"


def test_missing_duration_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command({"action": "activate_warning", "reason": "t"})
    assert excinfo.value.code == "INVALID_DURATION"


def test_overly_long_message_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command(
            {
                "action": "display_message",
                "reason": "t",
                "message": "This message is far too long for the LCD",
            }
        )
    assert excinfo.value.code == "MESSAGE_TOO_LONG"


def test_non_printable_message_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command(
            {"action": "display_message", "reason": "t", "message": "bad\x07bell"}
        )
    assert excinfo.value.code == "NON_PRINTABLE"


def test_unexpected_keys_rejected() -> None:
    with pytest.raises(CommandValidationError) as excinfo:
        validate_command(
            {
                "action": "request_status",
                "reason": "t",
                "shell": "rm -rf /",
            }
        )
    assert excinfo.value.code == "UNEXPECTED_KEY"


def test_serialisation_excludes_reason() -> None:
    command = validate_command(
        {"action": "activate_warning", "reason": "audit trail", "duration_seconds": 3}
    )
    payload = json.loads(to_serial_line(command).decode("ascii"))
    assert payload == {"action": "activate_warning", "duration_seconds": 3}
    assert "reason" not in payload


def test_serialised_line_is_newline_terminated() -> None:
    command = ValidatedCommand(action="request_status", reason="r")
    assert to_serial_line(command).endswith(b"\n")
