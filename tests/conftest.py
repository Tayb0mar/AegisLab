"""Shared pytest fixtures for the AegisLab backend test suite."""

from __future__ import annotations

import sys
from pathlib import Path

# Make `backend` importable when pytest runs from the repository root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from backend.config import Settings
from backend.database import MemoryDatabase


@pytest.fixture()
def settings() -> Settings:
    """Deterministic test settings; memory backend needs no credentials."""
    return Settings(
        mode="mock",
        db_backend="memory",
        reading_stale_after_seconds=8.0,
        device_offline_after_seconds=20.0,
        high_temperature_c=30.0,
        low_humidity_percent=30.0,
        dark_light_level=100,
        alert_cooldown_seconds=300.0,
        rapid_temperature_window_seconds=300.0,
        rapid_temperature_change_c=5.0,
        history_default_limit=100,
        history_max_limit=1000,
        simulator_interval_seconds=0.05,
        serial_timeout_seconds=0.2,
    )


@pytest.fixture()
def repo() -> MemoryDatabase:
    return MemoryDatabase()


class FakeClock:
    """Deterministic monotonic clock for cooldown/offline tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()
