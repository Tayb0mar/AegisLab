"""Mock serial device: lets the whole pipeline run without an Arduino.

:class:`MockSerialDevice` exposes the subset of the :mod:`pyserial` interface
the collector uses (``readline``, ``write``, ``close``, ``is_open``). It emits
one JSON reading at the configured interval with slowly drifting, plausible
values, always tagged ``"simulated": true`` so downstream layers and the
dashboard can distinguish it from real device data.

It also mirrors the firmware's command behaviour: valid allowlisted commands
receive a ``command_ack`` line, malformed ones a ``command_rejected`` line.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from typing import Any

PROTOCOL_VERSION = 1


class MockSerialDevice:
    """Drop-in stand-in for ``serial.Serial`` producing simulated readings."""

    def __init__(
        self,
        interval_seconds: float = 2.0,
        timeout: float = 2.0,
        seed: int | None = None,
        sensor_error_probability: float = 0.01,
    ) -> None:
        self._interval = max(0.05, interval_seconds)
        self._timeout = timeout
        self._random = random.Random(seed)
        self._sensor_error_probability = sensor_error_probability
        self._start = time.monotonic()
        self._next_emit = self._start + self._interval
        self._lock = threading.Lock()
        self._pending: list[bytes] = []
        self._motion_until = 0.0
        self._phase = self._random.uniform(0, 2 * math.pi)
        self.is_open = True

    # ------------------------------------------------------------ generation

    def _generate_reading(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self._start
        # Slow sinusoidal drift plus small noise: plausible indoor values.
        temperature = 23.0 + 3.0 * math.sin(elapsed / 120.0 + self._phase)
        temperature += self._random.uniform(-0.2, 0.2)
        humidity = 48.0 + 8.0 * math.sin(elapsed / 200.0 + self._phase / 2)
        humidity += self._random.uniform(-1.0, 1.0)
        light = 550 + int(200 * math.sin(elapsed / 90.0 + self._phase))
        light += self._random.randint(-20, 20)
        light = max(0, min(1023, light))

        now = time.monotonic()
        if now > self._motion_until and self._random.random() < 0.06:
            self._motion_until = now + self._random.uniform(2.0, 6.0)
        motion = now <= self._motion_until

        return {
            "v": PROTOCOL_VERSION,
            "uptime_ms": int(elapsed * 1000),
            "temperature": round(temperature, 1),
            "humidity": round(min(100.0, max(0.0, humidity)), 1),
            "light": light,
            "motion": motion,
            "status": "ok",
            "simulated": True,
        }

    def _emit_due_lines(self) -> None:
        now = time.monotonic()
        while now >= self._next_emit:
            self._next_emit += self._interval
            if self._random.random() < self._sensor_error_probability:
                error = {
                    "v": PROTOCOL_VERSION,
                    "status": "sensor_error",
                    "sensor": "dht",
                    "code": "READ_FAILED",
                    "simulated": True,
                }
                self._pending.append((json.dumps(error) + "\n").encode("utf-8"))
                continue
            reading = self._generate_reading()
            self._pending.append((json.dumps(reading) + "\n").encode("utf-8"))

    # ------------------------------------------------------- serial interface

    def readline(self) -> bytes:
        """Block until the next line or the timeout elapses (like pyserial)."""
        deadline = time.monotonic() + self._timeout
        while True:
            with self._lock:
                if not self.is_open:
                    return b""
                self._emit_due_lines()
                if self._pending:
                    return self._pending.pop(0)
                wait_until = min(deadline, self._next_emit)
            remaining = wait_until - time.monotonic()
            if remaining > 0:
                time.sleep(min(remaining, 0.05))
            if time.monotonic() >= deadline:
                with self._lock:
                    self._emit_due_lines()
                    if self._pending:
                        return self._pending.pop(0)
                return b""

    def write(self, data: bytes) -> int:
        """Emulate the firmware's command handling: ack or reject."""
        try:
            proposal = json.loads(data.decode("ascii").strip())
            action = proposal.get("action")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            proposal, action = None, None

        if action in ("activate_warning", "deactivate_warning", "display_message", "request_status"):
            response: dict[str, Any] = {
                "v": PROTOCOL_VERSION,
                "status": "command_ack",
                "action": action,
                "simulated": True,
            }
        else:
            response = {
                "v": PROTOCOL_VERSION,
                "status": "command_rejected",
                "code": "UNKNOWN_ACTION",
                "simulated": True,
            }
        with self._lock:
            self._pending.append((json.dumps(response) + "\n").encode("utf-8"))
        return len(data)

    def close(self) -> None:
        with self._lock:
            self.is_open = False
