"""Collector tests: end-to-end mock pipeline, error handling, offline alerts."""

from __future__ import annotations

import json
import time

import pytest

from backend.alert_engine import DEVICE_OFFLINE, SENSOR_FAILURE, AlertEngine
from backend.command_validator import validate_command
from backend.device_state import DeviceStateTracker
from backend.serial_reader import SerialCollector
from backend.simulator import MockSerialDevice
from tests.conftest import FakeClock


def build_collector(settings, repo, tracker=None, engine=None):
    tracker = tracker or DeviceStateTracker(
        stale_after_seconds=settings.reading_stale_after_seconds,
        offline_after_seconds=settings.device_offline_after_seconds,
    )
    engine = engine or AlertEngine(settings, repo)
    return SerialCollector(settings, repo, tracker, engine), tracker, engine


def wait_until(predicate, timeout=5.0, interval=0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# --------------------------------------------------------------- end to end


def test_mock_pipeline_stores_simulated_readings(settings, repo) -> None:
    collector, tracker, _ = build_collector(settings, repo)
    collector.start()
    try:
        assert wait_until(lambda: collector.stats.valid_readings >= 3)
    finally:
        collector.stop()

    readings = repo.readings(10, None)
    assert len(readings) >= 3
    assert all(r["source"] == "simulated" for r in readings)

    event_types = {e["event_type"] for e in repo.events(50)}
    assert "collector_started" in event_types
    assert "serial_connected" in event_types
    assert "collector_stopped" in event_types


def test_command_roundtrip_through_simulator(settings, repo) -> None:
    collector, _, _ = build_collector(settings, repo)
    collector.start()
    try:
        assert wait_until(lambda: collector.stats.valid_readings >= 1)
        command = validate_command(
            {"action": "request_status", "reason": "integration test"}
        )
        assert collector.send_command(command) is True
        assert wait_until(
            lambda: any(
                e["event_type"] == "command_sent" for e in repo.events(50)
            )
        )
    finally:
        collector.stop()


def test_send_command_rejects_raw_dicts(settings, repo) -> None:
    collector, _, _ = build_collector(settings, repo)
    with pytest.raises(TypeError):
        collector.send_command({"action": "request_status"})  # type: ignore[arg-type]


def test_send_command_without_connection_returns_false(settings, repo) -> None:
    collector, _, _ = build_collector(settings, repo)
    command = validate_command({"action": "request_status", "reason": "t"})
    assert collector.send_command(command) is False


# ------------------------------------------------------------ line handling


def test_malformed_line_does_not_crash(settings, repo) -> None:
    collector, _, _ = build_collector(settings, repo)
    collector._handle_line(b'{"temperature": 24.6,')
    assert collector.stats.malformed_messages == 1
    event_types = [e["event_type"] for e in repo.events(10)]
    assert "malformed_message" in event_types


def test_out_of_range_reading_rejected_not_stored(settings, repo) -> None:
    collector, _, _ = build_collector(settings, repo)
    bad = {"temperature": 24.0, "humidity": 200.0, "light": 100, "motion": False}
    collector._handle_line((json.dumps(bad) + "\n").encode())
    assert collector.stats.rejected_readings == 1
    assert repo.readings(10, None) == []


def test_sensor_error_line_creates_event_and_alert(settings, repo) -> None:
    collector, tracker, _ = build_collector(settings, repo)
    line = b'{"status":"sensor_error","sensor":"dht","code":"READ_FAILED"}\n'
    collector._handle_line(line)

    assert collector.stats.sensor_errors == 1
    assert tracker.snapshot().failing_sensors == ("dht",)
    alerts = repo.alerts(10, None, None)
    assert [a["alert_type"] for a in alerts] == [SENSOR_FAILURE]


def test_valid_line_persists_and_updates_tracker(settings, repo) -> None:
    collector, tracker, _ = build_collector(settings, repo)
    good = {"temperature": 24.0, "humidity": 50.0, "light": 500, "motion": True}
    collector._handle_line((json.dumps(good) + "\n").encode())
    assert collector.stats.valid_readings == 1
    stored = repo.latest_reading()
    assert stored["light_level"] == 500
    # mode=mock forces the simulated source label even without the flag
    assert stored["source"] == "simulated"
    assert tracker.snapshot().last_reading_at is not None


# --------------------------------------------------------- offline detection


def test_offline_transition_emits_alert_and_event(settings, repo) -> None:
    tracker_clock = FakeClock()
    engine_clock = FakeClock()
    tracker = DeviceStateTracker(
        stale_after_seconds=settings.reading_stale_after_seconds,
        offline_after_seconds=settings.device_offline_after_seconds,
        clock=tracker_clock,
    )
    engine = AlertEngine(settings, repo, clock=engine_clock)
    collector, _, _ = build_collector(settings, repo, tracker=tracker, engine=engine)

    tracker.on_serial_connected()
    tracker.on_valid_reading()
    collector._check_device_state()  # records ONLINE as the previous state

    tracker_clock.advance(25)  # beyond the 20 s offline threshold
    collector._check_device_state()

    alerts = repo.alerts(10, None, None)
    assert [a["alert_type"] for a in alerts] == [DEVICE_OFFLINE]
    transitions = [
        e["details"] for e in repo.events(20) if e["event_type"] == "device_state_changed"
    ]
    assert any("online -> offline" in detail for detail in transitions)


def test_offline_alert_not_repeated_without_transition(settings, repo) -> None:
    tracker_clock = FakeClock()
    tracker = DeviceStateTracker(8.0, 20.0, clock=tracker_clock)
    engine = AlertEngine(settings, repo, clock=FakeClock())
    collector, _, _ = build_collector(settings, repo, tracker=tracker, engine=engine)

    tracker.on_serial_connected()
    tracker.on_valid_reading()
    collector._check_device_state()
    tracker_clock.advance(25)
    collector._check_device_state()
    collector._check_device_state()  # still offline: no second alert

    alerts = repo.alerts(10, None, None)
    assert len(alerts) == 1


# ----------------------------------------------------------------- simulator


def test_simulator_lines_pass_validation(settings) -> None:
    from backend.validation import classify_message, parse_line, validate_reading

    device = MockSerialDevice(interval_seconds=0.01, timeout=1.0, seed=42,
                              sensor_error_probability=0.0)
    for _ in range(5):
        line = device.readline()
        message = parse_line(line)
        assert classify_message(message).value == "reading"
        reading = validate_reading(message, settings)
        assert reading.simulated is True
    device.close()


def test_simulator_acks_valid_commands_and_rejects_unknown(settings) -> None:
    device = MockSerialDevice(interval_seconds=60.0, timeout=0.5, seed=1)
    device.write(b'{"action":"request_status"}\n')
    response = json.loads(device.readline())
    assert response["status"] == "command_ack"

    device.write(b'{"action":"melt_down"}\n')
    response = json.loads(device.readline())
    assert response["status"] == "command_rejected"
    device.close()
