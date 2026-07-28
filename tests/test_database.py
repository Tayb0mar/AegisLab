"""Tests for the persistence layer.

The MySQL implementation is tested against a mocked ``mysql.connector`` so no
real server is needed: the tests assert that SQL is parameterised, commits and
rollbacks happen at the right time, and rows are mapped correctly. The
MemoryDatabase (used by the API tests and mock demos) is tested for behavioural
parity: ordering, foreign-key checks, filtering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mysql.connector
import pytest

from backend.config import Settings
from backend.database import DatabaseError, MemoryDatabase, MySQLDatabase

MYSQL_SETTINGS = Settings(db_backend="mysql", db_password="test-only")


@pytest.fixture()
def mocked_mysql():
    """MySQLDatabase wired to a fully mocked connector pool."""
    with patch("backend.database.pooling.MySQLConnectionPool") as pool_class:
        pool = MagicMock()
        connection = MagicMock()
        cursor = MagicMock()
        pool_class.return_value = pool
        pool.get_connection.return_value = connection
        connection.cursor.return_value = cursor
        database = MySQLDatabase(MYSQL_SETTINGS)
        yield database, connection, cursor


# ------------------------------------------------------------------- MySQL


def test_insert_reading_is_parameterised(mocked_mysql) -> None:
    database, connection, cursor = mocked_mysql
    cursor.lastrowid = 42

    reading_id = database.insert_reading(24.6, 51.2, 430, False, "device")

    assert reading_id == 42
    query, params = cursor.execute.call_args.args
    assert "%s" in query and "VALUES" in query
    assert params == (24.6, 51.2, 430, False, "device")
    # No value is ever interpolated into the SQL text itself.
    assert "24.6" not in query
    connection.commit.assert_called_once()
    connection.close.assert_called_once()


def test_query_failure_rolls_back(mocked_mysql) -> None:
    database, connection, cursor = mocked_mysql
    cursor.execute.side_effect = mysql.connector.Error(errno=1146)

    with pytest.raises(DatabaseError):
        database.insert_reading(24.6, 51.2, 430, False, "device")

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


def test_database_error_hides_credentials(mocked_mysql) -> None:
    database, _, cursor = mocked_mysql
    cursor.execute.side_effect = mysql.connector.Error(errno=1045)
    with pytest.raises(DatabaseError) as excinfo:
        database.insert_event("x", None)
    assert "test-only" not in str(excinfo.value)


def test_latest_reading_query_ordering(mocked_mysql) -> None:
    database, _, cursor = mocked_mysql
    cursor.fetchone.return_value = {
        "id": 7,
        "recorded_at": datetime(2026, 7, 26, 10, 0, 0),
        "temperature": None,
        "humidity": None,
        "light_level": 100,
        "motion": 1,
        "source": "device",
    }
    row = database.latest_reading()
    query = cursor.execute.call_args.args[0]
    assert "ORDER BY recorded_at DESC, id DESC" in query
    assert row["motion"] is True
    assert row["temperature"] is None
    assert row["recorded_at"].tzinfo == timezone.utc


def test_alerts_filters_are_parameterised(mocked_mysql) -> None:
    database, _, cursor = mocked_mysql
    cursor.fetchall.return_value = []
    database.alerts(10, acknowledged=False, severity="high")
    query, params = cursor.execute.call_args.args
    assert "acknowledged = %s" in query
    assert "severity = %s" in query
    assert params == (False, "high", 10)


def test_pool_creation_failure_raises_database_error() -> None:
    with patch(
        "backend.database.pooling.MySQLConnectionPool",
        side_effect=mysql.connector.Error(errno=2003),
    ):
        with pytest.raises(DatabaseError):
            MySQLDatabase(MYSQL_SETTINGS)


def test_ping_returns_false_on_failure(mocked_mysql) -> None:
    database, connection, cursor = mocked_mysql
    cursor.execute.side_effect = mysql.connector.Error(errno=2013)
    assert database.ping() is False


# ------------------------------------------------------------------ memory


def test_memory_insert_and_latest(repo: MemoryDatabase) -> None:
    first = repo.insert_reading(20.0, 40.0, 300, False, "simulated")
    second = repo.insert_reading(21.0, 41.0, 310, True, "simulated")
    assert second > first
    latest = repo.latest_reading()
    assert latest["id"] == second
    assert latest["motion"] is True


def test_memory_nullable_reading(repo: MemoryDatabase) -> None:
    reading_id = repo.insert_reading(None, None, None, False, "device")
    latest = repo.latest_reading()
    assert latest["id"] == reading_id
    assert latest["temperature"] is None


def test_memory_alert_foreign_key(repo: MemoryDatabase) -> None:
    with pytest.raises(DatabaseError):
        repo.insert_alert("HIGH_TEMPERATURE", "high", "msg", reading_id=999)
    reading_id = repo.insert_reading(30.0, 50.0, 100, False, "device")
    alert_id = repo.insert_alert("HIGH_TEMPERATURE", "high", "msg", reading_id)
    assert alert_id == 1


def test_memory_readings_pagination(repo: MemoryDatabase) -> None:
    ids = [repo.insert_reading(20.0 + i, 40.0, 300, False, "device") for i in range(5)]
    page = repo.readings(limit=2, before_id=None)
    assert [r["id"] for r in page] == [ids[4], ids[3]]
    next_page = repo.readings(limit=2, before_id=ids[3])
    assert [r["id"] for r in next_page] == [ids[2], ids[1]]


def test_memory_alert_filtering_and_ack(repo: MemoryDatabase) -> None:
    a1 = repo.insert_alert("A", "low", "m1", None)
    repo.insert_alert("B", "high", "m2", None)
    assert repo.acknowledge_alert(a1) is True
    assert repo.acknowledge_alert(999) is False

    unacked = repo.alerts(10, acknowledged=False, severity=None)
    assert [a["alert_type"] for a in unacked] == ["B"]
    high = repo.alerts(10, acknowledged=None, severity="high")
    assert [a["alert_type"] for a in high] == ["B"]


def test_memory_latest_alert_of_type(repo: MemoryDatabase) -> None:
    repo.insert_alert("A", "low", "old", None)
    newest = repo.insert_alert("A", "low", "new", None)
    found = repo.latest_alert_of_type("A")
    assert found["id"] == newest
    assert repo.latest_alert_of_type("MISSING") is None


def test_memory_events(repo: MemoryDatabase) -> None:
    repo.insert_event("serial_connected", "port=SIM")
    repo.insert_event("serial_disconnected", None)
    events = repo.events(10)
    assert events[0]["event_type"] == "serial_disconnected"
    assert events[1]["details"] == "port=SIM"
