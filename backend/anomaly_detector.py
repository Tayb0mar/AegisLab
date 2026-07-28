"""Advisory anomaly detection groundwork (FR-070..FR-072).

Version 1 ships a dependency-free rolling-statistics detector: each numeric
feature keeps a rolling window; a reading whose z-score exceeds the
configured threshold is flagged as a *possible unusual pattern* with an
explanation naming the contributing feature and its actual values. Output is
advisory only — it is recorded as a ``system_events`` row, never as an alert,
and must not be described as confirmed danger.

The :class:`AnomalyDetector` interface (``observe`` → optional
:class:`AnomalyVerdict`) is the seam where a trained model (e.g. an Isolation
Forest) can be plugged in later without touching the collector.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from backend.config import Settings
from backend.validation import ValidatedReading

_MIN_SAMPLES = 30  # do not judge anything before a minimal baseline exists


@dataclass(frozen=True)
class AnomalyVerdict:
    """A possible unusual pattern, with an interpretable explanation."""

    score: float
    explanation: str


class AnomalyDetector(Protocol):
    """Interface future model-based detectors must implement."""

    def observe(self, reading: ValidatedReading) -> AnomalyVerdict | None: ...


class RollingStatsAnomalyDetector:
    """Z-score detector over rolling windows of recent readings."""

    def __init__(self, settings: Settings) -> None:
        self._threshold = settings.anomaly_zscore_threshold
        size = max(_MIN_SAMPLES, settings.anomaly_window_size)
        self._windows: dict[str, deque[float]] = {
            "temperature": deque(maxlen=size),
            "humidity": deque(maxlen=size),
            "light": deque(maxlen=size),
        }

    @staticmethod
    def _zscore(window: deque[float], value: float) -> float | None:
        if len(window) < _MIN_SAMPLES:
            return None
        mean = sum(window) / len(window)
        variance = sum((x - mean) ** 2 for x in window) / len(window)
        std = math.sqrt(variance)
        if std < 1e-9:
            return None
        return (value - mean) / std

    def observe(self, reading: ValidatedReading) -> AnomalyVerdict | None:
        values = {
            "temperature": reading.temperature,
            "humidity": reading.humidity,
            "light": None if reading.light is None else float(reading.light),
        }

        worst: tuple[str, float, float] | None = None  # (feature, z, value)
        for feature, value in values.items():
            if value is None:
                continue
            window = self._windows[feature]
            z = self._zscore(window, value)
            window.append(value)
            if z is None:
                continue
            if worst is None or abs(z) > abs(worst[1]):
                worst = (feature, z, value)

        if worst is None or abs(worst[1]) < self._threshold:
            return None

        feature, z, value = worst
        window = self._windows[feature]
        mean = sum(window) / len(window)
        return AnomalyVerdict(
            score=abs(z),
            explanation=(
                f"Possible unusual pattern (advisory, not confirmed danger): "
                f"{feature}={value:.1f} deviates {abs(z):.1f} standard deviations "
                f"from the recent average {mean:.1f} over the last "
                f"{len(window)} readings."
            ),
        )
