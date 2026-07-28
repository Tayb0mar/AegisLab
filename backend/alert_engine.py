"""Rule-based alert engine with cooldown and duplicate suppression.

Rules are authoritative only with respect to the configured thresholds; the
engine never claims safety-critical meaning. Each rule produces an
:class:`AlertCandidate`; :meth:`AlertEngine.process` decides which candidates
are persisted:

* same alert type within the cooldown window and with an unchanged
  *signature* (a coarse bucket of the condition) → suppressed;
* a materially different condition (signature change, e.g. temperature
  escalated by another bucket) → new alert even inside the cooldown;
* after a restart the in-memory record is empty, so the newest alert of the
  same type is read from the database and used for the cooldown check
  (signatures are not stored in the database; this restart check is
  intentionally coarser and documented).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from backend.config import Settings
from backend.database import DatabaseError, Repository
from backend.validation import SensorErrorEvent, ValidatedReading

logger = logging.getLogger(__name__)

# Stable machine-readable alert types (FR-060).
HIGH_TEMPERATURE = "HIGH_TEMPERATURE"
LOW_HUMIDITY = "LOW_HUMIDITY"
UNUSUAL_DARKNESS = "UNUSUAL_DARKNESS"
MOTION_IN_DARK = "MOTION_IN_DARK"
RAPID_TEMPERATURE_CHANGE = "RAPID_TEMPERATURE_CHANGE"
SENSOR_FAILURE = "SENSOR_FAILURE"
DEVICE_OFFLINE = "DEVICE_OFFLINE"


@dataclass(frozen=True)
class AlertCandidate:
    """A rule that fired, before cooldown/duplicate filtering."""

    alert_type: str
    severity: str
    message: str
    reading_id: int | None = None
    signature: str = ""


@dataclass
class _EmissionRecord:
    monotonic_at: float
    signature: str


@dataclass
class EngineStats:
    """Counters exposed for observability and tests."""

    emitted: int = 0
    suppressed: int = 0
    persist_failures: int = 0
    last_suppressed_type: str | None = field(default=None)


class AlertEngine:
    """Evaluates rules against validated readings and persists alerts."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._clock = clock
        self._last_emitted: dict[str, _EmissionRecord] = {}
        # (monotonic_time, temperature) samples for the rapid-change window.
        self._temperature_window: deque[tuple[float, float]] = deque()
        self.stats = EngineStats()

    # ------------------------------------------------------------------ rules

    def evaluate_reading(
        self, reading: ValidatedReading, reading_id: int | None
    ) -> list[AlertCandidate]:
        """Run every reading-based rule and return the candidates that fired."""
        cfg = self._settings
        candidates: list[AlertCandidate] = []

        if reading.temperature is not None and reading.temperature >= cfg.high_temperature_c:
            excess_bucket = int(
                (reading.temperature - cfg.high_temperature_c) // 5
            )
            candidates.append(
                AlertCandidate(
                    alert_type=HIGH_TEMPERATURE,
                    severity="critical" if excess_bucket >= 1 else "high",
                    message=(
                        f"Temperature reached {reading.temperature:.1f}°C, above "
                        f"the {cfg.high_temperature_c:.1f}°C threshold."
                    ),
                    reading_id=reading_id,
                    signature=f"bucket:{excess_bucket}",
                )
            )

        if reading.humidity is not None and reading.humidity <= cfg.low_humidity_percent:
            deficit_bucket = int((cfg.low_humidity_percent - reading.humidity) // 10)
            candidates.append(
                AlertCandidate(
                    alert_type=LOW_HUMIDITY,
                    severity="medium" if deficit_bucket >= 1 else "low",
                    message=(
                        f"Humidity fell to {reading.humidity:.1f}%, below the "
                        f"{cfg.low_humidity_percent:.1f}% threshold."
                    ),
                    reading_id=reading_id,
                    signature=f"bucket:{deficit_bucket}",
                )
            )

        is_dark = reading.light is not None and reading.light <= cfg.dark_light_level
        if is_dark:
            candidates.append(
                AlertCandidate(
                    alert_type=UNUSUAL_DARKNESS,
                    severity="low",
                    message=(
                        f"Light level {reading.light} is at or below the dark "
                        f"threshold {cfg.dark_light_level}."
                    ),
                    reading_id=reading_id,
                    signature="dark",
                )
            )
            if reading.motion:
                candidates.append(
                    AlertCandidate(
                        alert_type=MOTION_IN_DARK,
                        severity="medium",
                        message=(
                            f"Motion detected while light level was {reading.light}, "
                            f"below the dark threshold {cfg.dark_light_level}."
                        ),
                        reading_id=reading_id,
                        signature="motion-dark",
                    )
                )

        rapid = self._evaluate_rapid_temperature(reading, reading_id)
        if rapid is not None:
            candidates.append(rapid)

        return candidates

    def _evaluate_rapid_temperature(
        self, reading: ValidatedReading, reading_id: int | None
    ) -> AlertCandidate | None:
        """Detect a temperature swing larger than the configured delta."""
        if reading.temperature is None:
            return None
        cfg = self._settings
        now = self._clock()
        window = self._temperature_window
        window.append((now, reading.temperature))
        cutoff = now - cfg.rapid_temperature_window_seconds
        while window and window[0][0] < cutoff:
            window.popleft()

        temps = [t for _, t in window]
        if len(temps) < 2:
            return None
        delta = max(temps) - min(temps)
        if delta < cfg.rapid_temperature_change_c:
            return None
        rising = temps[-1] >= temps[0]
        direction = "rose" if rising else "fell"
        return AlertCandidate(
            alert_type=RAPID_TEMPERATURE_CHANGE,
            severity="high",
            message=(
                f"Temperature {direction} by {delta:.1f}°C within "
                f"{cfg.rapid_temperature_window_seconds:.0f}s "
                f"(threshold {cfg.rapid_temperature_change_c:.1f}°C)."
            ),
            reading_id=reading_id,
            signature=f"direction:{direction}",
        )

    def evaluate_sensor_error(self, error: SensorErrorEvent) -> AlertCandidate:
        return AlertCandidate(
            alert_type=SENSOR_FAILURE,
            severity="medium",
            message=f"Sensor '{error.sensor}' reported failure code {error.code}.",
            signature=f"sensor:{error.sensor}",
        )

    def evaluate_device_offline(self, seconds_since_last: float | None) -> AlertCandidate:
        if seconds_since_last is None:
            detail = "No reading has ever been received."
        else:
            detail = f"Last reading was {seconds_since_last:.0f}s ago."
        return AlertCandidate(
            alert_type=DEVICE_OFFLINE,
            severity="high",
            message=(
                "Device appears offline: "
                + detail
                + f" Offline threshold is {self._settings.device_offline_after_seconds:.0f}s."
            ),
            signature="offline",
        )

    # ------------------------------------------------- cooldown + persistence

    def _is_suppressed(self, candidate: AlertCandidate) -> bool:
        cooldown = self._settings.alert_cooldown_seconds
        now = self._clock()

        record = self._last_emitted.get(candidate.alert_type)
        if record is not None:
            within_cooldown = (now - record.monotonic_at) < cooldown
            return within_cooldown and record.signature == candidate.signature

        # Fresh process: fall back to the database so a restart does not
        # instantly duplicate a recent alert.
        try:
            latest = self._repo.latest_alert_of_type(candidate.alert_type)
        except DatabaseError:
            logger.exception("cooldown lookup failed; allowing alert")
            return False
        if latest is None or latest["acknowledged"]:
            return False
        age = (datetime.now(timezone.utc) - latest["created_at"]).total_seconds()
        return age < cooldown

    def process(self, candidates: list[AlertCandidate]) -> list[int]:
        """Apply cooldown/dedup and persist surviving candidates.

        Returns the database ids of the alerts actually inserted.
        """
        inserted: list[int] = []
        for candidate in candidates:
            if self._is_suppressed(candidate):
                self.stats.suppressed += 1
                self.stats.last_suppressed_type = candidate.alert_type
                continue
            try:
                alert_id = self._repo.insert_alert(
                    candidate.alert_type,
                    candidate.severity,
                    candidate.message,
                    candidate.reading_id,
                )
            except DatabaseError:
                self.stats.persist_failures += 1
                logger.exception("failed to persist alert %s", candidate.alert_type)
                continue
            self._last_emitted[candidate.alert_type] = _EmissionRecord(
                monotonic_at=self._clock(), signature=candidate.signature
            )
            self.stats.emitted += 1
            inserted.append(alert_id)
        return inserted
