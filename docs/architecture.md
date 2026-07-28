# AegisLab — Architecture

## Overview

```text
DHT sensor ───┐
Photoresistor ┼─> Arduino Mega 2560 firmware (arduino/aegislab_firmware)
PIR sensor ───┘         │
                        │ newline-delimited JSON over USB serial (9600 baud)
                        v
        ┌────────────────────────────────────────────┐
        │  FastAPI process (backend/app.py)          │
        │                                            │
        │  SerialCollector thread (serial_reader.py) │
        │    parse → classify → validate → persist   │
        │    → alert rules → device health           │
        │                                            │
        │  API endpoints (/api/*, /health)           │
        │  Static dashboard (frontend/)              │
        └───────────────┬────────────────────────────┘
                        v
                 MySQL `aegislab`
        sensor_readings │ alerts │ system_events
```

## Key design decisions

**Single process, single serial owner.** The serial collector runs as a
background thread inside the FastAPI process. This removes the entire class of
bugs where two processes contend for the same COM port, and lets the API read
live device state (heartbeat, counters) without IPC. If you ever run the
collector separately, the API must be started with `AEGIS_MODE=off`.

**Three data-source modes** (`AEGIS_MODE`):

| Mode | Source | Use case |
|---|---|---|
| `mock` | `backend/simulator.py` | Development/demo without hardware (default) |
| `serial` | pyserial on `SERIAL_PORT` | Real Arduino |
| `off` | none | API-only (e.g. inspecting stored data) |

Simulated readings always carry `simulated: true` end-to-end (serial JSON →
`sensor_readings.source` column → `is_simulated` in the API → badge in the
dashboard), so mock data can never masquerade as real measurements.

**Two repository backends** (`DB_BACKEND`): `mysql` is the official store;
`memory` is an explicitly-volatile in-memory implementation of the same
interface used by the automated tests and for credential-free demos. It is not
a persistence option and is never selected implicitly.

**Layering.** Modules are separated by responsibility and are independently
testable:

| Module | Responsibility |
|---|---|
| `config.py` | All tunables from env/.env, fail-fast validation |
| `validation.py` | Serial trust boundary: parse/classify/range-check |
| `device_state.py` | STARTING/ONLINE/STALE/OFFLINE/SENSOR_ERROR machine |
| `database.py` | Repository interface + MySQL and memory implementations |
| `alert_engine.py` | Rules, severities, cooldown, dedup |
| `command_validator.py` | Allowlist gate for hardware commands |
| `simulator.py` | Mock serial device |
| `serial_reader.py` | Collector thread: pipeline orchestration |
| `anomaly_detector.py` | Advisory rolling-stats detector + model seam |
| `schemas.py` | Pydantic response models |
| `app.py` | FastAPI wiring, endpoints, lifecycle, static frontend |

## Processing sequence per line

1. `readline()` (2 s timeout so the loop always ticks).
2. UTF-8 decode, JSON parse, root-object check (`parse_line`).
3. Classify: reading / sensor_error / command_ack / command_rejected /
   device_status / unknown (`classify_message`).
4. Schema + type + physical-range validation (`validate_reading`).
5. One `INSERT` into `sensor_readings` (serial `light` → column `light_level`).
6. Alert rules evaluate the validated reading; survivors of the
   cooldown/dedup filter are persisted with the causing `reading_id`.
7. Device heartbeat updates; state transitions are logged to `system_events`
   and an OFFLINE transition raises a `DEVICE_OFFLINE` alert.
8. Optionally, the advisory anomaly detector records an
   `anomaly_advisory` system event (never an alert).

A malformed line increments a counter, is logged (rate-limited to one
`system_events` row per 10 s) and the loop continues.

## Device state machine

| State | Meaning |
|---|---|
| `starting` | Serial link up (or being attempted) but no valid reading yet |
| `online` | Valid reading within `READING_STALE_AFTER_SECONDS` |
| `stale` | Last reading older than stale threshold but not yet offline |
| `offline` | Serial down, or last reading older than `DEVICE_OFFLINE_AFTER_SECONDS` |
| `sensor_error` | Device alive but a sensor reported failure |

`GET /api/readings/latest` returns `is_stale: true` whenever the device is
not `online`/`sensor_error`, so the dashboard never presents old values as
live (FR-044/FR-052).

## Alert flow

```text
validated reading ─> rule evaluation ─> candidates
candidates ─> cooldown/signature filter ─> INSERT into alerts ─> API ─> dashboard
```

Cooldown: an alert of the same type within `ALERT_COOLDOWN_SECONDS` with an
unchanged *signature* is suppressed. The signature buckets the condition
(e.g. every 5 °C above the temperature threshold), so a materially worse
condition still alerts inside the cooldown. After a process restart the
newest unacknowledged alert of the same type in MySQL provides the fallback
cooldown check.

## LLM / hardware boundary

Free-form model output can never reach the serial port:

```text
proposal (untrusted) → command_validator.validate_command()
  → allowlist + bounds + unknown-key rejection
  → ValidatedCommand (canonical, typed)
  → to_serial_line() serialised by trusted code
  → SerialCollector.send_command() (accepts ONLY ValidatedCommand instances)
```

The firmware independently re-validates action names and bounds (defence in
depth) and answers with `command_ack` / `command_rejected` frames.
