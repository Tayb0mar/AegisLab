"""Tests for device health tracking and offline detection."""

from __future__ import annotations

from backend.device_state import DeviceState, DeviceStateTracker


def make_tracker(clock) -> DeviceStateTracker:
    return DeviceStateTracker(
        stale_after_seconds=8.0, offline_after_seconds=20.0, clock=clock
    )


def test_initial_state_without_connection_is_offline(clock) -> None:
    tracker = make_tracker(clock)
    assert tracker.snapshot().state == DeviceState.OFFLINE


def test_connected_but_no_reading_is_starting(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    snapshot = tracker.snapshot()
    assert snapshot.state == DeviceState.STARTING
    assert snapshot.last_reading_at is None
    assert snapshot.seconds_since_last_reading is None


def test_fresh_reading_is_online(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    tracker.on_valid_reading()
    clock.advance(2)
    snapshot = tracker.snapshot()
    assert snapshot.state == DeviceState.ONLINE
    assert snapshot.seconds_since_last_reading == 2


def test_reading_older_than_stale_threshold(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    tracker.on_valid_reading()
    clock.advance(9)
    assert tracker.snapshot().state == DeviceState.STALE


def test_reading_older_than_offline_threshold(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    tracker.on_valid_reading()
    clock.advance(21)
    assert tracker.snapshot().state == DeviceState.OFFLINE


def test_serial_disconnect_forces_offline_even_with_recent_reading(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    tracker.on_valid_reading()
    tracker.on_serial_disconnected()
    clock.advance(1)
    assert tracker.snapshot().state == DeviceState.OFFLINE


def test_reconnection_recovers(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    tracker.on_valid_reading()
    tracker.on_serial_disconnected()
    clock.advance(30)
    assert tracker.snapshot().state == DeviceState.OFFLINE

    tracker.on_serial_connected()
    tracker.on_valid_reading()
    assert tracker.snapshot().state == DeviceState.ONLINE


def test_sensor_error_state_and_recovery(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    tracker.on_valid_reading()
    tracker.on_sensor_error("dht")
    snapshot = tracker.snapshot()
    assert snapshot.state == DeviceState.SENSOR_ERROR
    assert snapshot.failing_sensors == ("dht",)

    # A subsequent full valid reading clears the failure.
    tracker.on_valid_reading()
    assert tracker.snapshot().state == DeviceState.ONLINE


def test_offline_takes_priority_over_sensor_error(clock) -> None:
    tracker = make_tracker(clock)
    tracker.on_serial_connected()
    tracker.on_valid_reading()
    tracker.on_sensor_error("dht")
    clock.advance(25)
    assert tracker.snapshot().state == DeviceState.OFFLINE
