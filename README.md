# AegisLab

An environmental monitoring system built around an Arduino Mega 2560. Sensors
on the Arduino stream JSON lines over USB serial to a Python collector, which
validates and stores the readings in **MySQL**, exposes them through a FastAPI
backend, and displays them on a small web dashboard. The backend also runs a
rule-based alert engine, tracks whether the device is online or offline, and
gates any outgoing hardware command through an allowlist before it reaches the
serial port.

> AegisLab is a personal/course project built with hobby sensors. It is
> **not** a certified safety device (no fire/medical/intrusion protection
> claims).

## Project status

- **Tested software:** the backend (validation, alert engine, device state
  machine, database layer, API, command validator) is covered by 118
  automated tests (`pytest`, see `docs/testing.md`). These run against a
  mocked serial driver and either a mocked MySQL connector or the in-memory
  repository — no real hardware or MySQL server is required.
- **Mock mode:** the full stack (backend + dashboard + alerts) runs
  end-to-end with simulated sensor data via `AEGIS_MODE=mock`. This is the
  easiest way to see the system working without any hardware.
- **Hardware, still to be verified physically:** the firmware has not been
  compiled/flashed in this environment, and the MySQL layer has not been
  exercised against a live server here (only against a mocked driver). See
  `docs/limitations.md` for the full list of what is and isn't verified.

## Features

- Firmware (C++) emitting one validated JSON object per serial line, with
  LCD/LED/bounded-buzzer outputs and an allowlisted command handler.
- Backend collector with strict boundary validation, automatic serial
  reconnection and system-event logging.
- MySQL persistence (`sensor_readings`, `alerts`, `system_events`) via
  parameterised queries and a connection pool.
- Rule-based alert engine (7 rule types) with cooldown and duplicate
  suppression.
- Device health state machine: `starting / online / stale / offline /
  sensor_error` — stale data is never presented as live.
- FastAPI API with automatic OpenAPI docs at `/docs`.
- Responsive dashboard: live values, history chart+table, alerts with
  acknowledge, system events, offline/degraded banners, and a clear
  **SIMULATED DATA** badge for mock data.
- **Mock mode**: the full system runs with zero hardware.
- Advisory anomaly-detection groundwork (rolling z-score, pluggable model
  interface).
- 118 automated tests.

## Repository layout

```text
AegisLab/
├── arduino/aegislab_firmware/   # firmware (.ino) + config.h (pins, thresholds)
├── backend/                     # Python package: collector, DB, API
├── frontend/                    # dashboard (HTML/CSS/JS, no build step)
├── sql/                         # schema.sql (idempotent) + seed_data.sql (demo)
├── tests/                       # pytest suite + Arduino component test sketches
├── docs/                        # architecture, protocol, API, setup, testing…
├── .env.example                 # configuration template (copy to .env)
└── README.md
```

## Quick start (Windows)

### 0. Prerequisites

- Python 3.13 (recommended)
- MySQL Server 8.x + MySQL Workbench (for the real database)
- Arduino IDE (only for real hardware)

### 1. Python environment

```bat
cd AegisLab
python -m venv venv
venv\Scripts\activate
pip install -r backend\requirements.txt
```

### 2. Configuration

```bat
copy .env.example .env
```

Edit `.env`. The three typical setups:

| Goal | Settings |
|---|---|
| Demo, no hardware, no MySQL | `AEGIS_MODE=mock`, `DB_BACKEND=memory` |
| Development with MySQL, no hardware | `AEGIS_MODE=mock`, `DB_BACKEND=mysql` + DB credentials |
| Real Arduino + MySQL | `AEGIS_MODE=serial`, `DB_BACKEND=mysql`, correct `SERIAL_PORT` |

`DB_BACKEND=memory` is volatile (demo/tests only). MySQL is the official
database.

### 3. Database (MySQL path)

Follow **`docs/database_setup.md`**: run `sql/schema.sql` in MySQL Workbench,
create the `aegislab_app` user, put the credentials in `.env`. Optionally run
`sql/seed_data.sql` for demo rows.

### 4. Run the backend (serves API **and** dashboard)

```bat
venv\Scripts\activate
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

- Dashboard: <http://127.0.0.1:8000/>
- API docs (OpenAPI): <http://127.0.0.1:8000/docs>
- Health: <http://127.0.0.1:8000/health>

The serial collector runs inside this process — do not start a second copy
against the same COM port, and keep the Arduino IDE Serial Monitor closed
while the backend runs.

### 5. Arduino (real hardware path)

Follow **`docs/arduino_setup.md`**: verify pins in
`arduino/aegislab_firmware/config.h`, install the DHT library, flash, check
the JSON stream, set `SERIAL_PORT` in `.env`, then start the backend with
`AEGIS_MODE=serial`.

### 6. Tests

```bat
venv\Scripts\activate
pytest
```

No MySQL or Arduino needed (mocked driver + simulator). See
`docs/testing.md` for the manual hardware acceptance matrix.

## Trying the alerts in mock mode

The simulator produces calm values, so rule alerts are rare by design. To see
the pipeline fire, lower a threshold, e.g. in `.env`:

```dotenv
HIGH_TEMPERATURE_C=24.0
ALERT_COOLDOWN_SECONDS=60
```

Restart the backend: `HIGH_TEMPERATURE` alerts appear on the dashboard
(acknowledge them with the button) and the cooldown prevents flooding.

Test a hardware command safely:

```bat
curl -X POST http://127.0.0.1:8000/api/commands -H "Content-Type: application/json" ^
     -d "{\"action\":\"activate_warning\",\"reason\":\"demo\",\"duration_seconds\":3}"
```

An unknown action or an excessive duration is rejected with `400` and never
reaches the device.

## Documentation index

| Document | Content |
|---|---|
| `docs/architecture.md` | Components, design decisions, data flow, command validation boundary |
| `docs/serial_protocol.md` | Serial contract v1 (frames, commands, validation) |
| `docs/api.md` | Endpoint reference, error shape, policies |
| `docs/database_setup.md` | MySQL Workbench walkthrough, least-privilege user |
| `docs/arduino_setup.md` | Wiring assumptions, libraries, flashing, simulation mode |
| `docs/testing.md` | Automated suite + manual acceptance matrix |
| `docs/limitations.md` | Known limitations — read before relying on the system |
| `docs/roadmap.md` | Next steps (model-based anomaly detection, …) |

## Configuration reference

Every tunable lives in `.env` (see `.env.example` for the full commented
list): serial port/baud/timeouts, staleness/offline thresholds, MySQL
credentials, API host/port and history limits, CORS origins, all alert
thresholds and cooldown, validation ranges, anomaly-detection settings.
Missing or inconsistent required configuration fails at startup with an
explicit message.

## Security posture

- No credentials in the repository; `.env` is gitignored.
- All SQL is parameterised; DB errors shown to clients are credential-free.
- Hardware commands pass a strict allowlist + bounds gate on the backend and
  are re-validated by the firmware; arbitrary/unvalidated text can never
  reach the serial port.
- The dashboard renders all dynamic data with `textContent` (no HTML
  injection), and CORS is restricted to configured origins.
