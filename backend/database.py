"""Persistence layer: a small repository interface with two implementations.

* :class:`MySQLDatabase` — the official backend (MySQL, parameterised queries,
  connection pool, UTC session time zone).
* :class:`MemoryDatabase` — volatile in-memory store used by automated tests
  and for demos when no MySQL credentials are available. It is NOT a
  persistence option and is only selected explicitly via ``DB_BACKEND=memory``.

Both return plain dictionaries with identical keys so the API layer does not
care which backend is active.
"""

from __future__ import annotations

import itertools
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

import mysql.connector
from mysql.connector import pooling

from backend.config import Settings

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Raised when a database operation fails. Message is credential-free."""


class Repository(Protocol):
    """Operations the rest of the application relies on."""

    def insert_reading(
        self,
        temperature: float | None,
        humidity: float | None,
        light_level: int | None,
        motion: bool,
        source: str,
    ) -> int: ...

    def insert_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        reading_id: int | None,
    ) -> int: ...

    def insert_event(self, event_type: str, details: str | None) -> int: ...

    def latest_reading(self) -> dict[str, Any] | None: ...

    def readings(self, limit: int, before_id: int | None) -> list[dict[str, Any]]: ...

    def alerts(
        self,
        limit: int,
        acknowledged: bool | None,
        severity: str | None,
    ) -> list[dict[str, Any]]: ...

    def acknowledge_alert(self, alert_id: int) -> bool: ...

    def latest_alert_of_type(self, alert_type: str) -> dict[str, Any] | None: ...

    def events(self, limit: int) -> list[dict[str, Any]]: ...

    def recent_temperatures(self, since_seconds: float) -> list[tuple[datetime, float]]: ...

    def ping(self) -> bool: ...

    def close(self) -> None: ...


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _utc(value: Any) -> datetime | None:
    """Interpret DB timestamps as UTC (the MySQL session runs in +00:00)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raise DatabaseError(f"unexpected timestamp type: {type(value)!r}")


def _reading_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "recorded_at": _utc(row["recorded_at"]),
        "temperature": _to_float(row["temperature"]),
        "humidity": _to_float(row["humidity"]),
        "light_level": row["light_level"],
        "motion": bool(row["motion"]),
        "source": row["source"],
    }


def _alert_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": _utc(row["created_at"]),
        "alert_type": row["alert_type"],
        "severity": row["severity"],
        "message": row["message"],
        "reading_id": row["reading_id"],
        "acknowledged": bool(row["acknowledged"]),
    }


def _event_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": _utc(row["created_at"]),
        "event_type": row["event_type"],
        "details": row["details"],
    }


class MySQLDatabase:
    """MySQL implementation backed by a small connection pool."""

    def __init__(self, settings: Settings, pool_size: int = 4) -> None:
        self._settings = settings
        try:
            self._pool = pooling.MySQLConnectionPool(
                pool_name="aegislab_pool",
                pool_size=pool_size,
                host=settings.db_host,
                port=settings.db_port,
                database=settings.db_name,
                user=settings.db_user,
                password=settings.db_password,
                autocommit=False,
                time_zone="+00:00",
            )
        except mysql.connector.Error as exc:
            # errno/msg from the driver may mention host/user but never the
            # password; still, keep our message generic.
            raise DatabaseError(
                f"cannot create MySQL connection pool (errno={exc.errno})"
            ) from exc

    def _execute(
        self,
        query: str,
        params: tuple[Any, ...] = (),
        *,
        fetch: str = "none",
    ) -> Any:
        """Run one parameterised statement on a pooled connection.

        ``fetch`` is one of ``none`` (returns lastrowid), ``one``, ``all``,
        ``rowcount``. Commits on success, rolls back on failure.
        """
        try:
            connection = self._pool.get_connection()
        except mysql.connector.Error as exc:
            raise DatabaseError(f"cannot obtain MySQL connection (errno={exc.errno})") from exc

        try:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(query, params)
                if fetch == "one":
                    result = cursor.fetchone()
                elif fetch == "all":
                    result = cursor.fetchall()
                elif fetch == "rowcount":
                    result = cursor.rowcount
                else:
                    result = cursor.lastrowid
                connection.commit()
                return result
            finally:
                cursor.close()
        except mysql.connector.Error as exc:
            try:
                connection.rollback()
            except mysql.connector.Error:
                logger.exception("rollback failed after query error")
            raise DatabaseError(f"query failed (errno={exc.errno})") from exc
        finally:
            connection.close()

    def insert_reading(
        self,
        temperature: float | None,
        humidity: float | None,
        light_level: int | None,
        motion: bool,
        source: str,
    ) -> int:
        return int(
            self._execute(
                "INSERT INTO sensor_readings"
                " (temperature, humidity, light_level, motion, source)"
                " VALUES (%s, %s, %s, %s, %s)",
                (temperature, humidity, light_level, motion, source),
            )
        )

    def insert_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        reading_id: int | None,
    ) -> int:
        return int(
            self._execute(
                "INSERT INTO alerts (alert_type, severity, message, reading_id)"
                " VALUES (%s, %s, %s, %s)",
                (alert_type, severity, message, reading_id),
            )
        )

    def insert_event(self, event_type: str, details: str | None) -> int:
        return int(
            self._execute(
                "INSERT INTO system_events (event_type, details) VALUES (%s, %s)",
                (event_type, details),
            )
        )

    def latest_reading(self) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT id, recorded_at, temperature, humidity, light_level, motion, source"
            " FROM sensor_readings ORDER BY recorded_at DESC, id DESC LIMIT 1",
            fetch="one",
        )
        return _reading_row(row) if row else None

    def readings(self, limit: int, before_id: int | None) -> list[dict[str, Any]]:
        if before_id is None:
            rows = self._execute(
                "SELECT id, recorded_at, temperature, humidity, light_level, motion, source"
                " FROM sensor_readings ORDER BY recorded_at DESC, id DESC LIMIT %s",
                (limit,),
                fetch="all",
            )
        else:
            rows = self._execute(
                "SELECT id, recorded_at, temperature, humidity, light_level, motion, source"
                " FROM sensor_readings WHERE id < %s"
                " ORDER BY recorded_at DESC, id DESC LIMIT %s",
                (before_id, limit),
                fetch="all",
            )
        return [_reading_row(row) for row in rows]

    def alerts(
        self,
        limit: int,
        acknowledged: bool | None,
        severity: str | None,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT id, created_at, alert_type, severity, message, reading_id, acknowledged"
            " FROM alerts"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if acknowledged is not None:
            conditions.append("acknowledged = %s")
            params.append(acknowledged)
        if severity is not None:
            conditions.append("severity = %s")
            params.append(severity)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC, id DESC LIMIT %s"
        params.append(limit)
        rows = self._execute(query, tuple(params), fetch="all")
        return [_alert_row(row) for row in rows]

    def acknowledge_alert(self, alert_id: int) -> bool:
        affected = self._execute(
            "UPDATE alerts SET acknowledged = TRUE WHERE id = %s",
            (alert_id,),
            fetch="rowcount",
        )
        return int(affected) > 0

    def latest_alert_of_type(self, alert_type: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT id, created_at, alert_type, severity, message, reading_id, acknowledged"
            " FROM alerts WHERE alert_type = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (alert_type,),
            fetch="one",
        )
        return _alert_row(row) if row else None

    def events(self, limit: int) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT id, created_at, event_type, details FROM system_events"
            " ORDER BY created_at DESC, id DESC LIMIT %s",
            (limit,),
            fetch="all",
        )
        return [_event_row(row) for row in rows]

    def recent_temperatures(self, since_seconds: float) -> list[tuple[datetime, float]]:
        rows = self._execute(
            "SELECT recorded_at, temperature FROM sensor_readings"
            " WHERE temperature IS NOT NULL"
            " AND recorded_at >= UTC_TIMESTAMP() - INTERVAL %s SECOND"
            " ORDER BY recorded_at ASC",
            (int(since_seconds),),
            fetch="all",
        )
        return [
            (_utc(row["recorded_at"]), float(_to_float(row["temperature"])))
            for row in rows
        ]

    def ping(self) -> bool:
        try:
            self._execute("SELECT 1", fetch="one")
            return True
        except DatabaseError:
            return False

    def close(self) -> None:
        # mysql.connector pools have no explicit close; pooled connections are
        # closed when the process exits.
        return None


class MemoryDatabase:
    """Thread-safe volatile store mirroring the MySQL row shapes exactly."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reading_ids = itertools.count(1)
        self._alert_ids = itertools.count(1)
        self._event_ids = itertools.count(1)
        self._readings: list[dict[str, Any]] = []
        self._alerts: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []

    def insert_reading(
        self,
        temperature: float | None,
        humidity: float | None,
        light_level: int | None,
        motion: bool,
        source: str,
    ) -> int:
        with self._lock:
            reading_id = next(self._reading_ids)
            self._readings.append(
                {
                    "id": reading_id,
                    "recorded_at": datetime.now(timezone.utc),
                    "temperature": temperature,
                    "humidity": humidity,
                    "light_level": light_level,
                    "motion": bool(motion),
                    "source": source,
                }
            )
            return reading_id

    def insert_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        reading_id: int | None,
    ) -> int:
        with self._lock:
            if reading_id is not None and not any(
                r["id"] == reading_id for r in self._readings
            ):
                raise DatabaseError(f"reading_id {reading_id} does not exist")
            alert_id = next(self._alert_ids)
            self._alerts.append(
                {
                    "id": alert_id,
                    "created_at": datetime.now(timezone.utc),
                    "alert_type": alert_type,
                    "severity": severity,
                    "message": message,
                    "reading_id": reading_id,
                    "acknowledged": False,
                }
            )
            return alert_id

    def insert_event(self, event_type: str, details: str | None) -> int:
        with self._lock:
            event_id = next(self._event_ids)
            self._events.append(
                {
                    "id": event_id,
                    "created_at": datetime.now(timezone.utc),
                    "event_type": event_type,
                    "details": details,
                }
            )
            return event_id

    def latest_reading(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._readings:
                return None
            newest = max(self._readings, key=lambda r: (r["recorded_at"], r["id"]))
            return dict(newest)

    def readings(self, limit: int, before_id: int | None) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(
                self._readings,
                key=lambda r: (r["recorded_at"], r["id"]),
                reverse=True,
            )
            if before_id is not None:
                rows = [r for r in rows if r["id"] < before_id]
            return [dict(r) for r in rows[:limit]]

    def alerts(
        self,
        limit: int,
        acknowledged: bool | None,
        severity: str | None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(
                self._alerts,
                key=lambda a: (a["created_at"], a["id"]),
                reverse=True,
            )
            if acknowledged is not None:
                rows = [a for a in rows if a["acknowledged"] == acknowledged]
            if severity is not None:
                rows = [a for a in rows if a["severity"] == severity]
            return [dict(a) for a in rows[:limit]]

    def acknowledge_alert(self, alert_id: int) -> bool:
        with self._lock:
            for alert in self._alerts:
                if alert["id"] == alert_id:
                    alert["acknowledged"] = True
                    return True
            return False

    def latest_alert_of_type(self, alert_type: str) -> dict[str, Any] | None:
        with self._lock:
            matching = [a for a in self._alerts if a["alert_type"] == alert_type]
            if not matching:
                return None
            newest = max(matching, key=lambda a: (a["created_at"], a["id"]))
            return dict(newest)

    def events(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(
                self._events,
                key=lambda e: (e["created_at"], e["id"]),
                reverse=True,
            )
            return [dict(e) for e in rows[:limit]]

    def recent_temperatures(self, since_seconds: float) -> list[tuple[datetime, float]]:
        cutoff = datetime.now(timezone.utc).timestamp() - since_seconds
        with self._lock:
            rows = [
                (r["recorded_at"], float(r["temperature"]))
                for r in self._readings
                if r["temperature"] is not None
                and r["recorded_at"].timestamp() >= cutoff
            ]
            rows.sort(key=lambda item: item[0])
            return rows

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


def create_repository(settings: Settings) -> Repository:
    """Instantiate the configured repository backend."""
    if settings.db_backend == "memory":
        logger.warning(
            "DB_BACKEND=memory: data is volatile and lost on restart. "
            "Use MySQL for real operation."
        )
        return MemoryDatabase()
    return MySQLDatabase(settings)
