"""Serial collector: reads the device (or simulator), validates, persists.

The collector runs as a single background thread owned by the FastAPI
application (see ``backend.app``). Running it inside the API process is a
deliberate design choice: it guarantees that exactly one reader owns the
serial port, avoiding the classic bug of two processes fighting over the same
COM device. Do not start a second collector against the same port.

Responsibilities per received line:
    read → decode → parse JSON → classify → validate → persist →
    evaluate alerts → update device health.

A malformed line never crashes the thread (NFR-001); serial disconnection
triggers bounded retry with a configurable delay (FR-023).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import serial as pyserial

from backend.alert_engine import AlertEngine
from backend.anomaly_detector import AnomalyDetector
from backend.command_validator import ValidatedCommand, to_serial_line
from backend.config import Settings
from backend.database import DatabaseError, Repository
from backend.device_state import DeviceState, DeviceStateTracker
from backend.simulator import MockSerialDevice
from backend.validation import (
    MessageType,
    ValidationError,
    classify_message,
    parse_line,
    validate_reading,
    validate_sensor_error,
)

logger = logging.getLogger(__name__)

# Minimum seconds between two 'malformed_message'/'unknown_message' rows so a
# broken device cannot flood system_events.
_NOISY_EVENT_MIN_INTERVAL = 10.0


class SerialLike(Protocol):
    """The minimal serial interface the collector needs (real or mock)."""

    is_open: bool

    def readline(self) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


@dataclass
class CollectorStats:
    """Counters for observability; read by the API status endpoint."""

    valid_readings: int = 0
    malformed_messages: int = 0
    rejected_readings: int = 0
    sensor_errors: int = 0
    reconnects: int = 0
    db_failures: int = 0


class SerialCollector:
    """Owns the serial source and drives the reading pipeline."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        tracker: DeviceStateTracker,
        engine: AlertEngine,
        anomaly_detector: AnomalyDetector | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repository
        self._tracker = tracker
        self._engine = engine
        self._anomaly = anomaly_detector
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._source: SerialLike | None = None
        self._source_lock = threading.Lock()
        self._last_noisy_event = 0.0
        self._previous_device_state: DeviceState | None = None
        self.stats = CollectorStats()

    # ---------------------------------------------------------------- control

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("collector already started")
        self._thread = threading.Thread(
            target=self._run, name="aegislab-collector", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        with self._source_lock:
            if self._source is not None:
                try:
                    self._source.close()
                except Exception:  # closing during shutdown must never raise
                    logger.exception("error while closing serial source")
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._safe_event("collector_stopped", None)

    # ---------------------------------------------------------------- source

    def _open_source(self) -> SerialLike:
        if self._settings.mode == "mock":
            return MockSerialDevice(
                interval_seconds=self._settings.simulator_interval_seconds,
                timeout=self._settings.serial_timeout_seconds,
            )
        return pyserial.Serial(
            port=self._settings.serial_port,
            baudrate=self._settings.baud_rate,
            timeout=self._settings.serial_timeout_seconds,
        )

    # ------------------------------------------------------------------ loop

    def _run(self) -> None:
        self._safe_event(
            "collector_started", f"mode={self._settings.mode}"
        )
        while not self._stop_event.is_set():
            try:
                source = self._open_source()
            except (pyserial.SerialException, OSError) as exc:
                self._tracker.on_serial_disconnected()
                logger.warning("serial connect failed: %s", exc)
                self._safe_event("serial_connect_failed", str(exc)[:500])
                self._check_device_state()
                self._stop_event.wait(self._settings.serial_reconnect_delay_seconds)
                continue

            with self._source_lock:
                self._source = source
            self._tracker.on_serial_connected()
            self._safe_event(
                "serial_connected",
                f"mode={self._settings.mode} port={self._settings.serial_port}",
            )

            try:
                self._read_loop(source)
            except (pyserial.SerialException, OSError) as exc:
                logger.warning("serial connection lost: %s", exc)
                self._safe_event("serial_disconnected", str(exc)[:500])
            finally:
                with self._source_lock:
                    self._source = None
                try:
                    source.close()
                except Exception:
                    logger.exception("error while closing serial source")
                self._tracker.on_serial_disconnected()

            if not self._stop_event.is_set():
                self.stats.reconnects += 1
                self._check_device_state()
                self._stop_event.wait(self._settings.serial_reconnect_delay_seconds)

    def _read_loop(self, source: SerialLike) -> None:
        while not self._stop_event.is_set():
            raw = source.readline()
            if raw:
                self._handle_line(raw)
            # Runs every iteration (readline times out after ~2s), so offline
            # detection works even when no data arrives at all.
            self._check_device_state()

    # ------------------------------------------------------------- processing

    def _handle_line(self, raw: bytes) -> None:
        try:
            message = parse_line(raw)
        except ValidationError as exc:
            if exc.code == "EMPTY_LINE":
                return
            self.stats.malformed_messages += 1
            logger.warning("malformed line rejected (%s): %s", exc.code, exc.message)
            self._noisy_event("malformed_message", f"{exc.code}: {exc.message}")
            return

        kind = classify_message(message)
        if kind == MessageType.READING:
            self._handle_reading(message)
        elif kind == MessageType.SENSOR_ERROR:
            self._handle_sensor_error(message)
        elif kind in (MessageType.COMMAND_ACK, MessageType.COMMAND_REJECTED):
            self._safe_event(kind.value, str(message)[:500])
        elif kind == MessageType.DEVICE_STATUS:
            self._safe_event("device_status", str(message)[:500])
        else:
            self.stats.malformed_messages += 1
            self._noisy_event("unknown_message", str(message)[:500])

    def _handle_reading(self, message: dict) -> None:
        try:
            reading = validate_reading(message, self._settings)
        except ValidationError as exc:
            self.stats.rejected_readings += 1
            logger.warning("reading rejected (%s): %s", exc.code, exc.message)
            self._noisy_event("invalid_reading", f"{exc.code}: {exc.message}")
            return

        source = "simulated" if (reading.simulated or self._settings.mode == "mock") else "device"
        try:
            reading_id = self._repo.insert_reading(
                reading.temperature,
                reading.humidity,
                reading.light,
                reading.motion,
                source,
            )
        except DatabaseError:
            self.stats.db_failures += 1
            logger.exception("failed to persist reading")
            return

        self.stats.valid_readings += 1
        self._tracker.on_valid_reading()

        candidates = self._engine.evaluate_reading(reading, reading_id)
        self._engine.process(candidates)

        if self._anomaly is not None and self._settings.anomaly_enabled:
            verdict = self._anomaly.observe(reading)
            if verdict is not None:
                self._safe_event("anomaly_advisory", verdict.explanation[:500])

        self._check_device_state()

    def _handle_sensor_error(self, message: dict) -> None:
        try:
            error = validate_sensor_error(message)
        except ValidationError as exc:
            self.stats.malformed_messages += 1
            self._noisy_event("malformed_message", f"{exc.code}: {exc.message}")
            return
        self.stats.sensor_errors += 1
        self._tracker.on_sensor_error(error.sensor)
        self._safe_event("sensor_error", f"sensor={error.sensor} code={error.code}")
        self._engine.process([self._engine.evaluate_sensor_error(error)])

    def _check_device_state(self) -> None:
        """Emit events/alerts on device state transitions (esp. OFFLINE)."""
        snapshot = self._tracker.snapshot()
        previous = self._previous_device_state
        self._previous_device_state = snapshot.state
        if previous is None or snapshot.state == previous:
            return

        self._safe_event(
            "device_state_changed", f"{previous.value} -> {snapshot.state.value}"
        )
        if snapshot.state == DeviceState.OFFLINE:
            candidate = self._engine.evaluate_device_offline(
                snapshot.seconds_since_last_reading
            )
            self._engine.process([candidate])

    # -------------------------------------------------------------- commands

    def send_command(self, command: ValidatedCommand) -> bool:
        """Write an already-validated command to the device.

        Only :class:`ValidatedCommand` instances are accepted; raw dicts or
        strings cannot reach the port through this method.
        """
        if not isinstance(command, ValidatedCommand):
            raise TypeError("send_command requires a ValidatedCommand")
        with self._source_lock:
            source = self._source
            if source is None or not source.is_open:
                return False
            try:
                source.write(to_serial_line(command))
            except (pyserial.SerialException, OSError) as exc:
                logger.warning("command write failed: %s", exc)
                return False
        self._safe_event(
            "command_sent",
            f"action={command.action} reason={command.reason[:120]}",
        )
        return True

    # --------------------------------------------------------------- logging

    def _safe_event(self, event_type: str, details: str | None) -> None:
        try:
            self._repo.insert_event(event_type, details)
        except DatabaseError:
            self.stats.db_failures += 1
            logger.warning("could not record system event %s", event_type)

    def _noisy_event(self, event_type: str, details: str) -> None:
        now = time.monotonic()
        if now - self._last_noisy_event < _NOISY_EVENT_MIN_INTERVAL:
            return
        self._last_noisy_event = now
        self._safe_event(event_type, details)
