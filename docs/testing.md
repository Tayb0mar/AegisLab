# AegisLab — Testing Guide

## Automated tests

Run from the repository root:

```bat
venv\Scripts\activate
pytest
```

Current suite: **118 tests** across 7 files (all passing at delivery time —
see the README delivery report):

| File | Covers |
|---|---|
| `tests/test_validation.py` | Parsing, classification, schema/type/range validation, non-finite values, boolean-as-number, boundaries, nulls |
| `tests/test_alert_engine.py` | Every rule at its boundary, severities, message content, cooldown, dedup via signature, materially-changed conditions, restart cooldown via DB, per-type independence |
| `tests/test_command_validator.py` | Full allowlist accepted, unknown action, bounds on duration/message, unexpected keys, reason audit, canonical serialisation |
| `tests/test_device_state.py` | STARTING/ONLINE/STALE/OFFLINE/SENSOR_ERROR transitions with an injected clock, reconnection recovery |
| `tests/test_database.py` | MySQL layer against a mocked connector (parameterised SQL, commit/rollback, credential-free errors, row mapping, UTC) + memory backend parity (FK check, ordering, pagination, filters) |
| `tests/test_api.py` | Every endpoint: shapes, no-data 404 policy, staleness flags, limit clamping/rejection, filters, acknowledge, command endpoint, degraded-DB 503 safety |
| `tests/test_collector.py` | End-to-end mock pipeline (simulator → validation → memory store → events), command roundtrip, malformed/out-of-range lines, sensor-error handling, offline transition alert, no repeated offline alerts |
| `tests/test_anomaly_detector.py` | Baseline requirement, stable series, outlier flagged with explanation, missing features |

Notes:

- Tests never require MySQL or an Arduino: the MySQL driver is mocked and the
  data-flow tests use the memory backend plus the simulator.
- A real end-to-end run against MySQL is an integration step, not part of the
  unit suite (see the manual matrix below).

## Manual acceptance matrix (hardware / integration)

These scenarios require a real Arduino and/or a running MySQL server and must
be executed on the physical setup:

| ID | Scenario | Status |
|---|---|---|
| AT-001 | Backend before Arduino → starting/offline, no crash | Verified in mock via unit tests; re-verify with hardware |
| AT-002 | Real readings enter MySQL | Requires hardware + MySQL |
| AT-003/004/005 | Cover light sensor / trigger PIR / warm DHT | Requires hardware |
| AT-006/007 | High-temperature alert + cooldown | Logic verified by tests; thresholds need real tuning |
| AT-008/009 | Disconnect/reconnect Arduino | Reconnect loop implemented; verify on real USB |
| AT-010–012 | Malformed/missing/out-of-range serial input | Verified by automated tests |
| AT-013 | Excessive API limit | Verified by automated tests |
| AT-014 | MySQL down → safe degraded responses | Verified by automated tests (fake failing repo); re-verify with the real service stopped |
| AT-015/016 | Unsupported command / excessive duration rejected | Verified by automated tests |
| AT-017 | Stale data never shown as live | Verified by automated tests + dashboard logic |
| AT-018 | No-data response | Verified by automated tests |

## Reproducing key scenarios by hand (no hardware)

```bat
:: mock mode without MySQL
set AEGIS_MODE=mock
set DB_BACKEND=memory
venv\Scripts\python -m uvicorn backend.app:app
```

- Open http://127.0.0.1:8000 — live simulated values with the SIMULATED DATA
  badge.
- Stop the server mid-session with the page open — the dashboard shows the
  red "API unreachable" banner and dims the cards.
- `curl -X POST http://127.0.0.1:8000/api/commands -H "Content-Type: application/json" -d "{\"action\":\"request_status\",\"reason\":\"test\"}"`
  → `202`; an invalid action → `400` and nothing reaches the (simulated)
  device.
