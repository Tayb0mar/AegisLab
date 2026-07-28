# AegisLab — API Reference

Base URL: `http://127.0.0.1:8000` (configurable via `API_HOST`/`API_PORT`).
Interactive OpenAPI docs are generated automatically at **`/docs`**
(Swagger UI) and **`/redoc`**.

All timestamps are ISO-8601 UTC with a `Z` suffix. All errors use one shape:

```json
{"error": {"code": "INVALID_LIMIT", "message": "limit must be between 1 and 1000"}}
```

Errors never contain credentials, connection strings or stack traces.

## `GET /health`

Liveness/degradation probe.

```json
{"status":"ok","database":"ok","collector":"running","mode":"mock","version":"1.0.0"}
```

`status` is `degraded` when MySQL is unreachable. `collector` is
`running`, `stopped`, or `disabled` (`AEGIS_MODE=off`).

## `GET /api/status`

```json
{
  "application": "online",
  "device": "online",
  "monitoring": true,
  "last_reading_at": "2026-07-26T10:06:49Z",
  "seconds_since_last_reading": 0.4,
  "mode": "mock",
  "data_source": "simulated",
  "counters": {
    "valid_readings": 12, "malformed_messages": 0, "rejected_readings": 0,
    "sensor_errors": 0, "reconnects": 0, "db_failures": 0
  }
}
```

`device` ∈ `starting | online | stale | offline | sensor_error`.
`data_source` ∈ `device | simulated | none`.

## `GET /api/readings/latest`

```json
{
  "id": 12, "recorded_at": "2026-07-26T10:06:49Z",
  "temperature": 25.4, "humidity": 54.9, "light": 736, "motion": false,
  "is_simulated": true, "is_stale": false, "device": "online"
}
```

The API exposes `light` even though MySQL stores `light_level`.
`is_stale` is `true` whenever the device is not fresh (stale/offline/
starting) — clients must not render such a reading as live.

**No-data policy (documented):** when no readings exist the endpoint returns
`404` with error code `NO_DATA`.

## `GET /api/readings`

Query parameters:

| Param | Type | Default | Rules |
|---|---|---|---|
| `limit` | int | `HISTORY_DEFAULT_LIMIT` (100) | 1..`HISTORY_MAX_LIMIT` (1000), else `400 INVALID_LIMIT` |
| `before` | int | — | id cursor, must be ≥ 1, else `400 INVALID_CURSOR` |

```json
{"items": [ ...ReadingItem, newest first... ], "count": 3, "limit": 3, "next_cursor": 11}
```

`next_cursor` is the id to pass as `before` for the next page; `null` when
the page was not full.

## `GET /api/alerts`

Query parameters: `limit` (default 50), `acknowledged` (bool),
`severity` (`low|medium|high|critical`, else `400 INVALID_SEVERITY`).

```json
{
  "items": [
    {"id": 7, "created_at": "2026-07-26T10:30:00Z", "type": "HIGH_TEMPERATURE",
     "severity": "high", "message": "Temperature reached 31.2°C, above the 30.0°C threshold.",
     "reading_id": 120, "acknowledged": false}
  ],
  "count": 1, "limit": 50
}
```

Alert types: `HIGH_TEMPERATURE`, `LOW_HUMIDITY`, `UNUSUAL_DARKNESS`,
`MOTION_IN_DARK`, `RAPID_TEMPERATURE_CHANGE`, `SENSOR_FAILURE`,
`DEVICE_OFFLINE`.

## `PATCH /api/alerts/{id}/acknowledge`

`200` → `{"id": 7, "acknowledged": true}`; unknown id → `404 NOT_FOUND`.

## `GET /api/events`

Query parameter `limit` (default 50). Returns system lifecycle events
(`collector_started`, `serial_connected`, `serial_disconnected`,
`device_state_changed`, `malformed_message`, `invalid_reading`,
`sensor_error`, `command_sent`, `command_ack`, `command_rejected`,
`anomaly_advisory`, …), newest first.

## `POST /api/commands`

Validates a hardware-command proposal and forwards it to the device.
Request body:

```json
{"action": "activate_warning", "reason": "alert-system test", "duration_seconds": 3}
```

Responses: `202 {"status":"sent","action":"activate_warning"}`;
`400` with the specific validation code (`UNKNOWN_ACTION`,
`DURATION_OUT_OF_RANGE`, `MESSAGE_TOO_LONG`, `UNEXPECTED_KEY`, …);
`409 DEVICE_UNAVAILABLE` when no device/simulator is connected.

## Degraded database

When MySQL is unreachable, data endpoints return
`503 {"error":{"code":"SERVICE_DEGRADED", ...}}` while `/health` and
`/api/status` keep answering so the dashboard can show a truthful degraded
state.
