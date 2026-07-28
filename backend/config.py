"""Central configuration loaded from environment variables (.env supported).

Every tunable value of the system lives here so that no module hides magic
numbers. Values that depend on real hardware (serial port, thresholds) are
placeholders to be adjusted once the physical setup is confirmed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

VALID_MODES = ("mock", "serial", "off")
VALID_DB_BACKENDS = ("mysql", "memory")


class ConfigurationError(Exception):
    """Raised when an environment value is missing or cannot be parsed."""


def _get_str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from exc


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from exc


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigurationError(f"{name} must be a boolean (true/false), got {raw!r}")


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of all runtime configuration."""

    # Operating mode
    mode: str = "mock"

    # Serial
    serial_port: str = "COM3"
    baud_rate: int = 9600
    serial_timeout_seconds: float = 2.0
    serial_reconnect_delay_seconds: float = 5.0

    # Device health
    reading_stale_after_seconds: float = 8.0
    device_offline_after_seconds: float = 20.0

    # Database
    db_backend: str = "mysql"
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "aegislab"
    db_user: str = "aegislab_app"
    db_password: str = ""

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    history_default_limit: int = 100
    history_max_limit: int = 1000
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )

    # Alert thresholds
    high_temperature_c: float = 30.0
    low_humidity_percent: float = 30.0
    dark_light_level: int = 100
    alert_cooldown_seconds: float = 300.0
    rapid_temperature_window_seconds: float = 300.0
    rapid_temperature_change_c: float = 5.0

    # Validation ranges (plausible physical bounds, not calibration)
    temperature_min_c: float = -40.0
    temperature_max_c: float = 85.0
    light_min: int = 0
    light_max: int = 1023

    # Anomaly detection (advisory only)
    anomaly_enabled: bool = False
    anomaly_window_size: int = 300
    anomaly_zscore_threshold: float = 3.5

    # Simulator pacing (mock mode)
    simulator_interval_seconds: float = 2.0

    extra: dict = field(default_factory=dict, compare=False)

    def validate(self) -> None:
        """Fail fast with a clear message when configuration is inconsistent."""
        if self.mode not in VALID_MODES:
            raise ConfigurationError(
                f"AEGIS_MODE must be one of {VALID_MODES}, got {self.mode!r}"
            )
        if self.db_backend not in VALID_DB_BACKENDS:
            raise ConfigurationError(
                f"DB_BACKEND must be one of {VALID_DB_BACKENDS}, got {self.db_backend!r}"
            )
        if self.db_backend == "mysql" and not self.db_password:
            raise ConfigurationError(
                "DB_PASSWORD is not set. Copy .env.example to .env and configure "
                "your MySQL credentials, or set DB_BACKEND=memory for a "
                "non-persistent demo."
            )
        if self.history_max_limit < self.history_default_limit:
            raise ConfigurationError(
                "HISTORY_MAX_LIMIT must be >= HISTORY_DEFAULT_LIMIT"
            )
        if self.reading_stale_after_seconds >= self.device_offline_after_seconds:
            raise ConfigurationError(
                "READING_STALE_AFTER_SECONDS must be smaller than "
                "DEVICE_OFFLINE_AFTER_SECONDS"
            )
        if self.temperature_min_c >= self.temperature_max_c:
            raise ConfigurationError("TEMPERATURE_MIN_C must be < TEMPERATURE_MAX_C")
        if self.light_min >= self.light_max:
            raise ConfigurationError("LIGHT_MIN must be < LIGHT_MAX")


def load_settings(dotenv_path: Path | None = None) -> Settings:
    """Build a validated :class:`Settings` from the environment and ``.env``."""
    load_dotenv(dotenv_path or PROJECT_ROOT / ".env", override=False)

    cors_raw = _get_str("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000")
    cors_origins = tuple(o.strip() for o in cors_raw.split(",") if o.strip())

    settings = Settings(
        mode=_get_str("AEGIS_MODE", "mock").lower(),
        serial_port=_get_str("SERIAL_PORT", "COM3"),
        baud_rate=_get_int("BAUD_RATE", 9600),
        serial_timeout_seconds=_get_float("SERIAL_TIMEOUT_SECONDS", 2.0),
        serial_reconnect_delay_seconds=_get_float("SERIAL_RECONNECT_DELAY_SECONDS", 5.0),
        reading_stale_after_seconds=_get_float("READING_STALE_AFTER_SECONDS", 8.0),
        device_offline_after_seconds=_get_float("DEVICE_OFFLINE_AFTER_SECONDS", 20.0),
        db_backend=_get_str("DB_BACKEND", "mysql").lower(),
        db_host=_get_str("DB_HOST", "127.0.0.1"),
        db_port=_get_int("DB_PORT", 3306),
        db_name=_get_str("DB_NAME", "aegislab"),
        db_user=_get_str("DB_USER", "aegislab_app"),
        db_password=os.environ.get("DB_PASSWORD", ""),
        api_host=_get_str("API_HOST", "127.0.0.1"),
        api_port=_get_int("API_PORT", 8000),
        history_default_limit=_get_int("HISTORY_DEFAULT_LIMIT", 100),
        history_max_limit=_get_int("HISTORY_MAX_LIMIT", 1000),
        cors_origins=cors_origins,
        high_temperature_c=_get_float("HIGH_TEMPERATURE_C", 30.0),
        low_humidity_percent=_get_float("LOW_HUMIDITY_PERCENT", 30.0),
        dark_light_level=_get_int("DARK_LIGHT_LEVEL", 100),
        alert_cooldown_seconds=_get_float("ALERT_COOLDOWN_SECONDS", 300.0),
        rapid_temperature_window_seconds=_get_float(
            "RAPID_TEMPERATURE_WINDOW_SECONDS", 300.0
        ),
        rapid_temperature_change_c=_get_float("RAPID_TEMPERATURE_CHANGE_C", 5.0),
        temperature_min_c=_get_float("TEMPERATURE_MIN_C", -40.0),
        temperature_max_c=_get_float("TEMPERATURE_MAX_C", 85.0),
        light_min=_get_int("LIGHT_MIN", 0),
        light_max=_get_int("LIGHT_MAX", 1023),
        anomaly_enabled=_get_bool("ANOMALY_ENABLED", False),
        anomaly_window_size=_get_int("ANOMALY_WINDOW_SIZE", 300),
        anomaly_zscore_threshold=_get_float("ANOMALY_ZSCORE_THRESHOLD", 3.5),
        simulator_interval_seconds=_get_float("SIMULATOR_INTERVAL_SECONDS", 2.0),
    )
    settings.validate()
    return settings
