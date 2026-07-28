"""API endpoint tests using the FastAPI TestClient and the memory backend."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.database import DatabaseError, MemoryDatabase
from backend.device_state import DeviceStateTracker
from backend.serial_reader import CollectorStats
from tests.conftest import FakeClock


class FailingRepo:
    """Repository stub whose every operation fails like a dead MySQL."""

    def ping(self) -> bool:
        return False

    def close(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        def _fail(*args: Any, **kwargs: Any) -> Any:
            raise DatabaseError("query failed (errno=2003)")

        return _fail


class FakeCollector:
    """Stands in for SerialCollector in command-endpoint tests."""

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.stats = CollectorStats()
        self.sent: list[Any] = []

    def send_command(self, command: Any) -> bool:
        if not self.connected:
            return False
        self.sent.append(command)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        return None


@pytest.fixture()
def client(settings, repo, clock):
    tracker = DeviceStateTracker(
        stale_after_seconds=settings.reading_stale_after_seconds,
        offline_after_seconds=settings.device_offline_after_seconds,
        clock=clock,
    )
    app = create_app(
        settings=settings, repository=repo, tracker=tracker, start_collector=False
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.tracker = tracker
        test_client.clock = clock
        yield test_client


@pytest.fixture()
def degraded_client(settings):
    app = create_app(
        settings=settings,
        repository=FailingRepo(),
        start_collector=False,
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# ------------------------------------------------------------------- health


def test_health_ok(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["mode"] == "mock"


def test_health_degraded(degraded_client) -> None:
    body = degraded_client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["database"] == "error"


# ------------------------------------------------------------------- status


def test_status_shape_and_starting_state(client) -> None:
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["application"] == "online"
    assert body["device"] == "offline"  # no serial link, no readings
    assert body["last_reading_at"] is None
    assert body["seconds_since_last_reading"] is None
    assert body["data_source"] == "simulated"
    assert body["monitoring"] is False


def test_status_online_after_reading(client, repo) -> None:
    client.tracker.on_serial_connected()
    client.tracker.on_valid_reading()
    client.clock.advance(1.5)
    body = client.get("/api/status").json()
    assert body["device"] == "online"
    assert body["seconds_since_last_reading"] == pytest.approx(1.5)
    assert body["last_reading_at"].endswith("Z")


def test_status_offline_after_timeout(client) -> None:
    client.tracker.on_serial_connected()
    client.tracker.on_valid_reading()
    client.clock.advance(25)
    body = client.get("/api/status").json()
    assert body["device"] == "offline"


# ------------------------------------------------------------------- latest


def test_latest_without_data_returns_documented_404(client) -> None:
    response = client.get("/api/readings/latest")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_DATA"


def test_latest_returns_reading_with_freshness(client, repo) -> None:
    repo.insert_reading(24.6, 51.2, 430, False, "simulated")
    client.tracker.on_serial_connected()
    client.tracker.on_valid_reading()
    body = client.get("/api/readings/latest").json()
    assert body["temperature"] == 24.6
    assert body["light"] == 430  # DB column light_level exposed as light
    assert body["is_simulated"] is True
    assert body["is_stale"] is False
    assert body["device"] == "online"


def test_latest_is_marked_stale_when_device_offline(client, repo) -> None:
    repo.insert_reading(24.6, 51.2, 430, False, "simulated")
    client.tracker.on_serial_connected()
    client.tracker.on_valid_reading()
    client.clock.advance(30)
    body = client.get("/api/readings/latest").json()
    assert body["is_stale"] is True
    assert body["device"] == "offline"


# ----------------------------------------------------------------- readings


def test_readings_default_page(client, repo) -> None:
    for i in range(5):
        repo.insert_reading(20.0 + i, 50.0, 300, False, "simulated")
    body = client.get("/api/readings").json()
    assert body["count"] == 5
    assert body["limit"] == 100
    assert body["next_cursor"] is None
    temps = [item["temperature"] for item in body["items"]]
    assert temps == [24.0, 23.0, 22.0, 21.0, 20.0]  # newest first


def test_readings_pagination_cursor(client, repo) -> None:
    ids = [repo.insert_reading(20.0, 50.0, 300, False, "simulated") for _ in range(5)]
    first = client.get("/api/readings?limit=2").json()
    assert first["next_cursor"] == ids[3]
    second = client.get(f"/api/readings?limit=2&before={first['next_cursor']}").json()
    assert [item["id"] for item in second["items"]] == [ids[2], ids[1]]


def test_readings_excessive_limit_rejected(client) -> None:
    response = client.get("/api/readings?limit=5000")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_LIMIT"


def test_readings_non_numeric_limit_rejected(client) -> None:
    response = client.get("/api/readings?limit=abc")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"


def test_readings_invalid_cursor_rejected(client) -> None:
    response = client.get("/api/readings?before=0")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


# ------------------------------------------------------------------- alerts


def test_alerts_filtering(client, repo) -> None:
    a1 = repo.insert_alert("HIGH_TEMPERATURE", "high", "hot", None)
    repo.insert_alert("LOW_HUMIDITY", "low", "dry", None)
    repo.acknowledge_alert(a1)

    everything = client.get("/api/alerts").json()
    assert everything["count"] == 2
    assert everything["items"][0]["type"] == "LOW_HUMIDITY"

    unacked = client.get("/api/alerts?acknowledged=false").json()
    assert [item["type"] for item in unacked["items"]] == ["LOW_HUMIDITY"]

    high = client.get("/api/alerts?severity=high").json()
    assert [item["type"] for item in high["items"]] == ["HIGH_TEMPERATURE"]


def test_alerts_invalid_severity_rejected(client) -> None:
    response = client.get("/api/alerts?severity=apocalyptic")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SEVERITY"


def test_acknowledge_alert(client, repo) -> None:
    alert_id = repo.insert_alert("HIGH_TEMPERATURE", "high", "hot", None)
    response = client.patch(f"/api/alerts/{alert_id}/acknowledge")
    assert response.status_code == 200
    assert response.json() == {"id": alert_id, "acknowledged": True}
    assert repo.alerts(10, None, None)[0]["acknowledged"] is True


def test_acknowledge_missing_alert_returns_404(client) -> None:
    response = client.patch("/api/alerts/999/acknowledge")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------- events


def test_events_listing(client, repo) -> None:
    repo.insert_event("serial_connected", "port=SIM")
    body = client.get("/api/events").json()
    assert body["count"] == 1
    assert body["items"][0]["event_type"] == "serial_connected"


# ----------------------------------------------------------------- commands


def test_valid_command_forwarded(client) -> None:
    collector = FakeCollector()
    client.app.state.context.collector = collector
    response = client.post(
        "/api/commands",
        json={"action": "activate_warning", "reason": "demo", "duration_seconds": 3},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "sent", "action": "activate_warning"}
    assert len(collector.sent) == 1


def test_unknown_command_rejected_before_transmission(client) -> None:
    collector = FakeCollector()
    client.app.state.context.collector = collector
    response = client.post(
        "/api/commands", json={"action": "launch_missiles", "reason": "no"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_ACTION"
    assert collector.sent == []  # nothing reached the device


def test_command_with_excessive_duration_rejected(client) -> None:
    response = client.post(
        "/api/commands",
        json={"action": "activate_warning", "reason": "t", "duration_seconds": 60},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DURATION_OUT_OF_RANGE"


def test_command_without_collector_returns_conflict(client) -> None:
    response = client.post(
        "/api/commands", json={"action": "request_status", "reason": "t"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DEVICE_UNAVAILABLE"


def test_command_with_disconnected_device_returns_conflict(client) -> None:
    client.app.state.context.collector = FakeCollector(connected=False)
    response = client.post(
        "/api/commands", json={"action": "request_status", "reason": "t"}
    )
    assert response.status_code == 409


# ------------------------------------------------------------ degraded mode


def test_degraded_database_yields_safe_503(degraded_client) -> None:
    for path in ("/api/readings/latest", "/api/readings", "/api/alerts", "/api/events"):
        response = degraded_client.get(path)
        assert response.status_code == 503, path
        body = response.json()
        assert body["error"]["code"] == "SERVICE_DEGRADED"
        text = response.text.lower()
        assert "password" not in text
        assert "traceback" not in text


def test_degraded_status_still_answers(degraded_client) -> None:
    body = degraded_client.get("/api/status").json()
    assert body["application"] == "degraded"
