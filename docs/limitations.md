# AegisLab — Known Limitations

## Scope and safety

- **AegisLab is not a safety device.** It must not be used for fire
  detection, medical monitoring, intrusion protection or any safety-critical
  purpose. Alerts are best-effort conveniences based on hobby hardware.
- Hobby sensors (DHT-class, photoresistors, PIR modules) have significant
  tolerance and drift; readings are indicative, not calibrated measurements.
- Alert thresholds are configuration choices with development defaults; they
  have not been tuned against real measured behaviour yet.
- Anomaly detection (when enabled) is a simple rolling z-score and produces
  false positives and false negatives by design; its output is advisory and
  is recorded as a system event, never as an alert.

## Architecture

- Single device, single serial port, local-only deployment. No
  authentication or user accounts: do not expose the API beyond localhost /
  a trusted LAN.
- USB disconnection interrupts monitoring until the reconnect loop
  re-establishes the link; readings during the gap are lost (the device does
  not buffer).
- The collector thread lives inside the API process. Running multiple API
  processes (e.g. `uvicorn --workers 2`) would start multiple collectors —
  always run a single worker, which is uvicorn's default.
- `DB_BACKEND=memory` is volatile by design: restart = empty database. It
  exists for tests and credential-free demos only.
- The alert-cooldown fallback after a restart compares only alert type and
  acknowledgement (signatures are in-memory), so a restart can occasionally
  suppress an escalation it would otherwise emit.
- `system_events` logging of malformed input is rate-limited (1 row / 10 s);
  the full count is still visible in `/api/status` counters.

## Software verification status

- Everything except physical-hardware behaviour is covered by 118 automated
  tests. The MySQL layer is verified against a mocked driver; it has not yet
  been exercised against a live MySQL server from this environment (no
  credentials available at build time). Follow `docs/database_setup.md` and
  run the smoke checks there.
- The firmware compiles against the Arduino core, the Adafruit DHT library
  and LiquidCrystal, but has not been compiled or flashed in this
  environment — verify with the Arduino IDE before first use.
- Timestamps assume the MySQL session time zone `+00:00` set by the backend;
  external tools writing rows with local-time defaults would mix time zones.
