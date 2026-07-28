"""Tests for the advisory rolling-statistics anomaly detector."""

from __future__ import annotations

import dataclasses

from backend.anomaly_detector import RollingStatsAnomalyDetector
from backend.validation import ValidatedReading


def reading(temperature: float) -> ValidatedReading:
    return ValidatedReading(
        temperature=temperature, humidity=50.0, light=500, motion=False
    )


def make_detector(settings) -> RollingStatsAnomalyDetector:
    return RollingStatsAnomalyDetector(
        dataclasses.replace(settings, anomaly_enabled=True)
    )


def test_no_verdict_before_baseline_exists(settings) -> None:
    detector = make_detector(settings)
    for _ in range(10):
        assert detector.observe(reading(24.0)) is None


def test_stable_series_is_not_anomalous(settings) -> None:
    detector = make_detector(settings)
    verdicts = [detector.observe(reading(24.0 + (i % 3) * 0.1)) for i in range(100)]
    assert all(v is None for v in verdicts)


def test_outlier_is_flagged_with_explanation(settings) -> None:
    detector = make_detector(settings)
    for i in range(60):
        detector.observe(reading(24.0 + (i % 5) * 0.1))
    verdict = detector.observe(reading(80.0))
    assert verdict is not None
    assert verdict.score >= settings.anomaly_zscore_threshold
    assert "temperature" in verdict.explanation
    assert "80.0" in verdict.explanation
    assert "advisory" in verdict.explanation.lower()


def test_missing_features_are_skipped(settings) -> None:
    detector = make_detector(settings)
    partial = ValidatedReading(temperature=None, humidity=None, light=None, motion=False)
    assert detector.observe(partial) is None
