"""Pydantic response models: the stable, documented API surface.

Timestamps are serialised as ISO-8601 UTC strings with a ``Z`` suffix, so the
models use ``str`` fields and the API layer formats datetimes explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel


def iso_utc(value: datetime | None) -> str | None:
    """Format a datetime as ISO-8601 UTC with a trailing Z."""
    if value is None:
        return None
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    collector: Literal["running", "stopped", "disabled"]
    mode: Literal["mock", "serial", "off"]
    version: str


class StatusResponse(BaseModel):
    application: Literal["online", "degraded"]
    device: Literal["starting", "online", "stale", "offline", "sensor_error"]
    monitoring: bool
    last_reading_at: str | None
    seconds_since_last_reading: float | None
    mode: Literal["mock", "serial", "off"]
    data_source: Literal["device", "simulated", "none"]
    counters: dict[str, int]


class ReadingItem(BaseModel):
    id: int
    recorded_at: str
    temperature: float | None
    humidity: float | None
    light: int | None
    motion: bool
    is_simulated: bool


class LatestReadingResponse(ReadingItem):
    is_stale: bool
    device: Literal["starting", "online", "stale", "offline", "sensor_error"]


class ReadingsPage(BaseModel):
    items: list[ReadingItem]
    count: int
    limit: int
    next_cursor: int | None


class AlertItem(BaseModel):
    id: int
    created_at: str
    type: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str
    reading_id: int | None
    acknowledged: bool


class AlertsPage(BaseModel):
    items: list[AlertItem]
    count: int
    limit: int


class AcknowledgeResponse(BaseModel):
    id: int
    acknowledged: Literal[True]


class EventItem(BaseModel):
    id: int
    created_at: str
    event_type: str
    details: str | None


class EventsPage(BaseModel):
    items: list[EventItem]
    count: int
    limit: int


class CommandAccepted(BaseModel):
    status: Literal["sent"]
    action: str
