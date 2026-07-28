"""Device health tracking: STARTING → ONLINE → STALE → OFFLINE transitions.

The tracker is deliberately independent from the serial code so the state
machine can be unit-tested with an injected clock. All methods are
thread-safe: the collector thread updates it while API requests read it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


class DeviceState(str, Enum):
    STARTING = "starting"
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"
    SENSOR_ERROR = "sensor_error"


@dataclass(frozen=True)
class DeviceSnapshot:
    """Consistent view of device health at one instant."""

    state: DeviceState
    last_reading_at: datetime | None
    seconds_since_last_reading: float | None
    serial_connected: bool
    failing_sensors: tuple[str, ...]


class DeviceStateTracker:
    """Derives the device state from heartbeats and connection events."""

    def __init__(
        self,
        stale_after_seconds: float,
        offline_after_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stale_after = stale_after_seconds
        self._offline_after = offline_after_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_reading_monotonic: float | None = None
        self._last_reading_wall: datetime | None = None
        self._serial_connected = False
        self._failing_sensors: set[str] = set()

    def on_serial_connected(self) -> None:
        with self._lock:
            self._serial_connected = True

    def on_serial_disconnected(self) -> None:
        with self._lock:
            self._serial_connected = False

    def on_valid_reading(self) -> None:
        with self._lock:
            self._last_reading_monotonic = self._clock()
            self._last_reading_wall = datetime.now(timezone.utc)
            # A full valid reading means every sensor produced usable data.
            self._failing_sensors.clear()

    def on_sensor_error(self, sensor: str) -> None:
        with self._lock:
            self._failing_sensors.add(sensor)

    def snapshot(self) -> DeviceSnapshot:
        """Compute the current state without mutating anything."""
        with self._lock:
            now = self._clock()
            last_mono = self._last_reading_monotonic
            elapsed = None if last_mono is None else now - last_mono

            if last_mono is None:
                # No valid reading has ever arrived. While the serial link is
                # up (or still being attempted) this is STARTING; if the link
                # is down the truthful answer is OFFLINE.
                state = (
                    DeviceState.STARTING if self._serial_connected else DeviceState.OFFLINE
                )
            elif elapsed is not None and elapsed > self._offline_after:
                state = DeviceState.OFFLINE
            elif not self._serial_connected:
                state = DeviceState.OFFLINE
            elif elapsed is not None and elapsed > self._stale_after:
                state = DeviceState.STALE
            elif self._failing_sensors:
                state = DeviceState.SENSOR_ERROR
            else:
                state = DeviceState.ONLINE

            return DeviceSnapshot(
                state=state,
                last_reading_at=self._last_reading_wall,
                seconds_since_last_reading=elapsed,
                serial_connected=self._serial_connected,
                failing_sensors=tuple(sorted(self._failing_sensors)),
            )
