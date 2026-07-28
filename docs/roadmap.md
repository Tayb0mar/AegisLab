# AegisLab — Next Steps

## Hardware bring-up (first)

1. Confirm sensor models and wiring; update `arduino/aegislab_firmware/config.h`.
2. Flash the firmware, verify the JSON stream in the Serial Monitor.
3. Point `.env` at the real COM port, switch `AEGIS_MODE=serial`, run the
   manual acceptance matrix (`docs/testing.md`).
4. Tune thresholds (`HIGH_TEMPERATURE_C`, `DARK_LIGHT_LEVEL`, cooldown) from
   observed data.

## Version 1.x candidates

- **Anomaly detection, model-based.** The `AnomalyDetector` protocol in
  `backend/anomaly_detector.py` is the seam: train an Isolation Forest on
  collected normal data (persist with joblib under `data/`), implement
  `observe()` for it, and surface advisories in the dashboard. Keep output
  advisory and explained (FR-071/072).
- **Alert-state table** replacing the type+cooldown heuristic with explicit
  open/closed alert lifecycles (noted in the DB spec as a future
  improvement).
- **Dashboard command panel** using `POST /api/commands` (test warning,
  display message) with confirmation UI.
- **WebSocket push** instead of 3 s polling.
- **Retention job** for `sensor_readings`/`system_events` growth.
- **Historical charts** for humidity/light and time-range selection.

## Explicitly out of scope for v1 (per specification)

RFID, actuators beyond LED/buzzer, user accounts, cloud hosting, mobile app,
multiple devices, unrestricted LLM hardware control, safety-critical claims.
