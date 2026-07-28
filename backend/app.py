"""FastAPI application: API endpoints, static dashboard, collector lifecycle.

The app owns every runtime component (repository, device tracker, alert
engine, serial collector). The collector runs as one background thread inside
this process so a single reader owns the serial port.

Error policy: every error response uses the shape
``{"error": {"code": ..., "message": ...}}`` and never leaks credentials,
connection strings or stack traces.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.alert_engine import AlertEngine
from backend.anomaly_detector import RollingStatsAnomalyDetector
from backend.command_validator import CommandValidationError, validate_command
from backend.config import PROJECT_ROOT, Settings, load_settings
from backend.database import DatabaseError, Repository, create_repository
from backend.device_state import DeviceState, DeviceStateTracker
from backend.schemas import (
    AcknowledgeResponse,
    AlertItem,
    AlertsPage,
    CommandAccepted,
    ErrorResponse,
    EventItem,
    EventsPage,
    HealthResponse,
    LatestReadingResponse,
    ReadingItem,
    ReadingsPage,
    StatusResponse,
    iso_utc,
)
from backend.serial_reader import SerialCollector

logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"
FRONTEND_DIR = PROJECT_ROOT / "frontend"

VALID_SEVERITIES = ("low", "medium", "high", "critical")


class ApiError(Exception):
    """Application-level error carrying a stable code and HTTP status."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass
class AppContext:
    """Everything the endpoints need, created during startup."""

    settings: Settings
    repository: Repository | None = None
    tracker: DeviceStateTracker | None = None
    engine: AlertEngine | None = None
    collector: SerialCollector | None = None
    db_error: bool = field(default=False)


def _context(request: Request) -> AppContext:
    return request.app.state.context


def _require_repo(ctx: AppContext) -> Repository:
    if ctx.repository is None:
        raise ApiError(
            503,
            "SERVICE_DEGRADED",
            "The database is currently unavailable. Check the MySQL service "
            "and the configured credentials.",
        )
    return ctx.repository


def _reading_to_item(row: dict) -> dict:
    return {
        "id": row["id"],
        "recorded_at": iso_utc(row["recorded_at"]),
        "temperature": row["temperature"],
        "humidity": row["humidity"],
        "light": row["light_level"],
        "motion": row["motion"],
        "is_simulated": row["source"] == "simulated",
    }


def _alert_to_item(row: dict) -> dict:
    return {
        "id": row["id"],
        "created_at": iso_utc(row["created_at"]),
        "type": row["alert_type"],
        "severity": row["severity"],
        "message": row["message"],
        "reading_id": row["reading_id"],
        "acknowledged": row["acknowledged"],
    }


def _validate_limit(limit: int | None, default: int, maximum: int) -> int:
    if limit is None:
        return default
    if limit < 1 or limit > maximum:
        raise ApiError(
            400, "INVALID_LIMIT", f"limit must be between 1 and {maximum}"
        )
    return limit


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
    tracker: DeviceStateTracker | None = None,
    start_collector: bool = True,
) -> FastAPI:
    """Build the application.

    Tests inject ``settings``/``repository``/``tracker`` and usually pass
    ``start_collector=False``; production leaves everything to the lifespan.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = AppContext(settings=settings or load_settings())
        app.state.context = ctx

        ctx.tracker = tracker or DeviceStateTracker(
            stale_after_seconds=ctx.settings.reading_stale_after_seconds,
            offline_after_seconds=ctx.settings.device_offline_after_seconds,
        )

        if repository is not None:
            ctx.repository = repository
        else:
            try:
                ctx.repository = create_repository(ctx.settings)
                if not ctx.repository.ping():
                    raise DatabaseError("initial ping failed")
            except DatabaseError:
                logger.exception(
                    "database unavailable at startup; running degraded"
                )
                ctx.repository = None
                ctx.db_error = True

        if ctx.repository is not None:
            ctx.engine = AlertEngine(ctx.settings, ctx.repository)
            if ctx.settings.mode != "off" and start_collector:
                anomaly = (
                    RollingStatsAnomalyDetector(ctx.settings)
                    if ctx.settings.anomaly_enabled
                    else None
                )
                ctx.collector = SerialCollector(
                    ctx.settings,
                    ctx.repository,
                    ctx.tracker,
                    ctx.engine,
                    anomaly_detector=anomaly,
                )
                ctx.collector.start()

        yield

        if ctx.collector is not None:
            ctx.collector.stop()
        if ctx.repository is not None:
            ctx.repository.close()

    app = FastAPI(
        title="AegisLab API",
        description=(
            "Local environmental monitoring prototype: sensor readings, "
            "rule-based alerts and system events collected from an Arduino "
            "Mega 2560 (or a built-in simulator in mock mode). AegisLab is "
            "not a certified safety device."
        ),
        version=APP_VERSION,
        lifespan=lifespan,
        responses={500: {"model": ErrorResponse}},
    )

    # CORS: needed when the dashboard is opened from a different origin than
    # the API (e.g. a separate static server). Same-origin use works without.
    lazy_settings = settings  # may be None; origins fall back to defaults
    origins = list(
        (lazy_settings.cors_origins if lazy_settings else Settings().cors_origins)
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "PATCH", "POST"],
        allow_headers=["Content-Type"],
    )

    # ------------------------------------------------------- error handlers

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", []))
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_PARAMETER",
                    "message": f"invalid value for {location or 'request'}",
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred.",
                }
            },
        )

    # ------------------------------------------------------------- endpoints

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health(request: Request) -> dict:
        ctx = _context(request)
        db_ok = ctx.repository is not None and ctx.repository.ping()
        if ctx.settings.mode == "off":
            collector_state = "disabled"
        elif ctx.collector is not None:
            collector_state = "running"
        else:
            collector_state = "stopped"
        return {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "error",
            "collector": collector_state,
            "mode": ctx.settings.mode,
            "version": APP_VERSION,
        }

    @app.get(
        "/api/status",
        response_model=StatusResponse,
        responses={503: {"model": ErrorResponse}},
        tags=["system"],
    )
    async def status(request: Request) -> dict:
        ctx = _context(request)
        snapshot = ctx.tracker.snapshot()
        db_ok = ctx.repository is not None and ctx.repository.ping()

        if ctx.settings.mode == "mock":
            data_source = "simulated"
        elif ctx.settings.mode == "serial":
            data_source = "device"
        else:
            data_source = "none"

        counters: dict[str, int] = {}
        if ctx.collector is not None:
            stats = ctx.collector.stats
            counters = {
                "valid_readings": stats.valid_readings,
                "malformed_messages": stats.malformed_messages,
                "rejected_readings": stats.rejected_readings,
                "sensor_errors": stats.sensor_errors,
                "reconnects": stats.reconnects,
                "db_failures": stats.db_failures,
            }

        return {
            "application": "online" if db_ok else "degraded",
            "device": snapshot.state.value,
            "monitoring": ctx.collector is not None,
            "last_reading_at": iso_utc(snapshot.last_reading_at),
            "seconds_since_last_reading": (
                None
                if snapshot.seconds_since_last_reading is None
                else round(snapshot.seconds_since_last_reading, 1)
            ),
            "mode": ctx.settings.mode,
            "data_source": data_source,
            "counters": counters,
        }

    @app.get(
        "/api/readings/latest",
        response_model=LatestReadingResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["readings"],
    )
    async def latest_reading(request: Request) -> dict:
        ctx = _context(request)
        repo = _require_repo(ctx)
        try:
            row = repo.latest_reading()
        except DatabaseError as exc:
            raise ApiError(
                503, "SERVICE_DEGRADED", "The database query failed."
            ) from exc
        if row is None:
            # Documented no-data policy: 404 with the standard error shape.
            raise ApiError(404, "NO_DATA", "No readings have been stored yet.")

        snapshot = ctx.tracker.snapshot()
        is_stale = snapshot.state in (
            DeviceState.STALE,
            DeviceState.OFFLINE,
            DeviceState.STARTING,
        )
        item = _reading_to_item(row)
        item["is_stale"] = is_stale
        item["device"] = snapshot.state.value
        return item

    @app.get(
        "/api/readings",
        response_model=ReadingsPage,
        responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["readings"],
    )
    async def readings(
        request: Request,
        limit: int | None = Query(default=None),
        before: int | None = Query(default=None),
    ) -> dict:
        ctx = _context(request)
        repo = _require_repo(ctx)
        bounded = _validate_limit(
            limit,
            ctx.settings.history_default_limit,
            ctx.settings.history_max_limit,
        )
        if before is not None and before < 1:
            raise ApiError(400, "INVALID_CURSOR", "before must be a positive id")
        try:
            rows = repo.readings(bounded, before)
        except DatabaseError as exc:
            raise ApiError(
                503, "SERVICE_DEGRADED", "The database query failed."
            ) from exc
        items = [_reading_to_item(row) for row in rows]
        next_cursor = rows[-1]["id"] if len(rows) == bounded and rows else None
        return {
            "items": items,
            "count": len(items),
            "limit": bounded,
            "next_cursor": next_cursor,
        }

    @app.get(
        "/api/alerts",
        response_model=AlertsPage,
        responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["alerts"],
    )
    async def alerts(
        request: Request,
        limit: int | None = Query(default=None),
        acknowledged: bool | None = Query(default=None),
        severity: str | None = Query(default=None),
    ) -> dict:
        ctx = _context(request)
        repo = _require_repo(ctx)
        bounded = _validate_limit(limit, 50, ctx.settings.history_max_limit)
        if severity is not None and severity not in VALID_SEVERITIES:
            raise ApiError(
                400,
                "INVALID_SEVERITY",
                f"severity must be one of {', '.join(VALID_SEVERITIES)}",
            )
        try:
            rows = repo.alerts(bounded, acknowledged, severity)
        except DatabaseError as exc:
            raise ApiError(
                503, "SERVICE_DEGRADED", "The database query failed."
            ) from exc
        items = [_alert_to_item(row) for row in rows]
        return {"items": items, "count": len(items), "limit": bounded}

    @app.patch(
        "/api/alerts/{alert_id}/acknowledge",
        response_model=AcknowledgeResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["alerts"],
    )
    async def acknowledge(request: Request, alert_id: int) -> dict:
        ctx = _context(request)
        repo = _require_repo(ctx)
        try:
            updated = repo.acknowledge_alert(alert_id)
        except DatabaseError as exc:
            raise ApiError(
                503, "SERVICE_DEGRADED", "The database update failed."
            ) from exc
        if not updated:
            raise ApiError(404, "NOT_FOUND", f"alert {alert_id} does not exist")
        return {"id": alert_id, "acknowledged": True}

    @app.get(
        "/api/events",
        response_model=EventsPage,
        responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["events"],
    )
    async def events(
        request: Request, limit: int | None = Query(default=None)
    ) -> dict:
        ctx = _context(request)
        repo = _require_repo(ctx)
        bounded = _validate_limit(limit, 50, ctx.settings.history_max_limit)
        try:
            rows = repo.events(bounded)
        except DatabaseError as exc:
            raise ApiError(
                503, "SERVICE_DEGRADED", "The database query failed."
            ) from exc
        items = [
            {
                "id": row["id"],
                "created_at": iso_utc(row["created_at"]),
                "event_type": row["event_type"],
                "details": row["details"],
            }
            for row in rows
        ]
        return {"items": items, "count": len(items), "limit": bounded}

    @app.post(
        "/api/commands",
        response_model=CommandAccepted,
        status_code=202,
        responses={
            400: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["commands"],
    )
    async def send_command(request: Request, proposal: dict) -> dict:
        """Validate a hardware command proposal and forward it to the device.

        Only allowlisted, bounds-checked commands ever reach the serial port
        (FR-080..FR-084). The full proposal, including the audit ``reason``,
        is validated here; the reason itself is never transmitted.
        """
        ctx = _context(request)
        try:
            command = validate_command(proposal)
        except CommandValidationError as exc:
            raise ApiError(400, exc.code, exc.message) from exc

        if ctx.collector is None:
            raise ApiError(
                409,
                "DEVICE_UNAVAILABLE",
                "No collector is running (AEGIS_MODE=off), so commands "
                "cannot be delivered.",
            )
        if not ctx.collector.send_command(command):
            raise ApiError(
                409,
                "DEVICE_UNAVAILABLE",
                "The device is not connected; the command was not sent.",
            )
        return {"status": "sent", "action": command.action}

    # Static dashboard served by the same origin as the API (documented
    # decision: no second web server needed on Windows).
    if FRONTEND_DIR.is_dir():
        app.mount(
            "/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend"
        )

    return app


app = create_app()


def main() -> None:
    """Run the API with uvicorn using host/port from configuration."""
    import uvicorn

    runtime_settings = load_settings()
    uvicorn.run(
        "backend.app:app",
        host=runtime_settings.api_host,
        port=runtime_settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
