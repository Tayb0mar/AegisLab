"""Tests for the rule engine: boundaries, cooldown, dedup, messages."""

from __future__ import annotations

import pytest

from backend.alert_engine import (
    DEVICE_OFFLINE,
    HIGH_TEMPERATURE,
    LOW_HUMIDITY,
    MOTION_IN_DARK,
    RAPID_TEMPERATURE_CHANGE,
    SENSOR_FAILURE,
    UNUSUAL_DARKNESS,
    AlertEngine,
)
from backend.validation import SensorErrorEvent, ValidatedReading


def reading(**overrides) -> ValidatedReading:
    base = dict(temperature=24.0, humidity=50.0, light=500, motion=False)
    base.update(overrides)
    return ValidatedReading(**base)


@pytest.fixture()
def engine(settings, repo, clock) -> AlertEngine:
    return AlertEngine(settings, repo, clock=clock)


# ------------------------------------------------------------------- rules


def test_normal_reading_produces_no_alert(engine) -> None:
    assert engine.evaluate_reading(reading(), 1) == []


def test_high_temperature_boundary(engine) -> None:
    assert engine.evaluate_reading(reading(temperature=29.9), 1) == []
    candidates = engine.evaluate_reading(reading(temperature=30.0), 2)
    types = [c.alert_type for c in candidates]
    assert HIGH_TEMPERATURE in types
    alert = next(c for c in candidates if c.alert_type == HIGH_TEMPERATURE)
    assert "30.0" in alert.message  # threshold value present in the text
    assert alert.reading_id == 2
    assert alert.severity == "high"


def test_high_temperature_escalates_to_critical(engine) -> None:
    candidates = engine.evaluate_reading(reading(temperature=36.0), 1)
    alert = next(c for c in candidates if c.alert_type == HIGH_TEMPERATURE)
    assert alert.severity == "critical"


def test_low_humidity_boundary(engine) -> None:
    assert engine.evaluate_reading(reading(humidity=30.1), 1) == []
    candidates = engine.evaluate_reading(reading(humidity=30.0), 2)
    assert [c.alert_type for c in candidates] == [LOW_HUMIDITY]
    assert "30.0" in candidates[0].message


def test_darkness_boundary(engine) -> None:
    assert engine.evaluate_reading(reading(light=101), 1) == []
    candidates = engine.evaluate_reading(reading(light=100), 2)
    assert [c.alert_type for c in candidates] == [UNUSUAL_DARKNESS]


def test_motion_in_dark(engine) -> None:
    candidates = engine.evaluate_reading(reading(light=50, motion=True), 1)
    types = [c.alert_type for c in candidates]
    assert UNUSUAL_DARKNESS in types
    assert MOTION_IN_DARK in types


def test_motion_in_light_is_not_an_alert(engine) -> None:
    assert engine.evaluate_reading(reading(light=500, motion=True), 1) == []


def test_rapid_temperature_change(engine, clock) -> None:
    assert engine.evaluate_reading(reading(temperature=22.0), 1) == []
    clock.advance(60)
    candidates = engine.evaluate_reading(reading(temperature=27.5), 2)
    assert [c.alert_type for c in candidates] == [RAPID_TEMPERATURE_CHANGE]
    assert "5.5" in candidates[0].message


def test_rapid_change_ignores_samples_outside_window(engine, clock) -> None:
    engine.evaluate_reading(reading(temperature=22.0), 1)
    clock.advance(400)  # beyond the 300 s window
    assert engine.evaluate_reading(reading(temperature=27.5), 2) == []


def test_sensor_failure_candidate(engine) -> None:
    candidate = engine.evaluate_sensor_error(
        SensorErrorEvent(sensor="dht", code="READ_FAILED")
    )
    assert candidate.alert_type == SENSOR_FAILURE
    assert "dht" in candidate.message


def test_device_offline_candidate(engine) -> None:
    candidate = engine.evaluate_device_offline(42.0)
    assert candidate.alert_type == DEVICE_OFFLINE
    assert "42" in candidate.message


# --------------------------------------------------- cooldown & persistence


def test_process_persists_alert(engine, repo) -> None:
    reading_id = repo.insert_reading(31.0, 50.0, 500, False, "simulated")
    candidates = engine.evaluate_reading(reading(temperature=31.0), reading_id)
    inserted = engine.process(candidates)
    assert len(inserted) == 1
    stored = repo.alerts(10, None, None)
    assert stored[0]["alert_type"] == HIGH_TEMPERATURE
    assert stored[0]["reading_id"] == reading_id


def test_cooldown_suppresses_duplicates(engine, repo, clock) -> None:
    first = engine.process(engine.evaluate_reading(reading(temperature=31.0), None))
    assert len(first) == 1
    clock.advance(10)  # well inside the 300 s cooldown
    second = engine.process(engine.evaluate_reading(reading(temperature=31.2), None))
    assert second == []
    assert engine.stats.suppressed == 1


def test_alert_re_emitted_after_cooldown(engine, repo, clock) -> None:
    engine.process(engine.evaluate_reading(reading(temperature=31.0), None))
    clock.advance(301)
    inserted = engine.process(engine.evaluate_reading(reading(temperature=31.0), None))
    assert len(inserted) == 1


def test_materially_different_condition_creates_new_alert(engine, repo, clock) -> None:
    engine.process(engine.evaluate_reading(reading(temperature=31.0), None))
    clock.advance(10)
    # 37 °C is in the next escalation bucket → new alert despite cooldown.
    # (The jump also legitimately fires RAPID_TEMPERATURE_CHANGE, so assert
    # on the HIGH_TEMPERATURE rows specifically.)
    engine.process(engine.evaluate_reading(reading(temperature=37.0), None))
    high = [
        a for a in repo.alerts(10, None, None) if a["alert_type"] == HIGH_TEMPERATURE
    ]
    assert len(high) == 2


def test_restart_uses_database_for_cooldown(settings, repo, clock) -> None:
    first_engine = AlertEngine(settings, repo, clock=clock)
    first_engine.process(first_engine.evaluate_reading(reading(temperature=31.0), None))

    # New engine instance = simulated restart. DB row is recent → suppressed.
    second_engine = AlertEngine(settings, repo, clock=clock)
    result = second_engine.process(
        second_engine.evaluate_reading(reading(temperature=31.0), None)
    )
    assert result == []


def test_acknowledged_alert_does_not_block_after_restart(settings, repo, clock) -> None:
    first_engine = AlertEngine(settings, repo, clock=clock)
    ids = first_engine.process(
        first_engine.evaluate_reading(reading(temperature=31.0), None)
    )
    repo.acknowledge_alert(ids[0])

    second_engine = AlertEngine(settings, repo, clock=clock)
    result = second_engine.process(
        second_engine.evaluate_reading(reading(temperature=31.0), None)
    )
    assert len(result) == 1


def test_different_alert_types_do_not_share_cooldown(engine, repo, clock) -> None:
    engine.process(engine.evaluate_reading(reading(temperature=31.0), None))
    clock.advance(1)
    # Same temperature (HIGH_TEMPERATURE suppressed by cooldown) but new low
    # humidity: the LOW_HUMIDITY alert must still be emitted.
    inserted = engine.process(
        engine.evaluate_reading(reading(temperature=31.0, humidity=25.0), None)
    )
    assert len(inserted) == 1
    assert repo.alerts(10, None, None)[0]["alert_type"] == LOW_HUMIDITY
