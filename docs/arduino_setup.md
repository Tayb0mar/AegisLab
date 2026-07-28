# AegisLab — Arduino Setup Guide

## Hardware (to be confirmed against your kit)

Target board: **Arduino Mega 2560**. The default pin map in
`arduino/aegislab_firmware/config.h` matches the component test sketches in
`tests/` and must be verified against your actual wiring:

| Component | Default pin(s) | Config constant |
|---|---|---|
| DHT temperature/humidity (DHT11 assumed) | 2 | `DHT_PIN`, `DHT_SENSOR_TYPE` |
| PIR motion sensor | 3 | `PIR_PIN` |
| Photoresistor divider (analog) | A0 | `LIGHT_SENSOR_PIN` |
| Warning LED (with series resistor) | 22 | `LED_PIN` |
| Buzzer | 23 | `BUZZER_PIN` |
| LCD 16x2 (4-bit parallel) | RS 7, E 8, D4–D7 9–12 | `LCD_*` |

If any component differs (e.g. DHT22 instead of DHT11, I2C LCD instead of
parallel), change **only** `config.h` — no pin or model is hard-coded in the
sketch body. An I2C LCD would additionally require swapping the
`LiquidCrystal` calls for a `LiquidCrystal_I2C` equivalent; this is the one
change that goes beyond `config.h`.

## Required libraries

Install via Arduino IDE → Library Manager:

- **DHT sensor library** (Adafruit) + its dependency **Adafruit Unified
  Sensor** — same library used by `tests/04_dht11_test`.
- **LiquidCrystal** — bundled with the IDE.

## Flashing

1. Open `arduino/aegislab_firmware/aegislab_firmware.ino` in the Arduino IDE
   (the IDE loads `config.h` from the same folder automatically).
2. Review `config.h`: pins, `DHT_SENSOR_TYPE`, thresholds.
3. Tools → Board → *Arduino Mega or Mega 2560*.
4. Tools → Port → note the COM port (e.g. `COM3`); put the same value in
   `.env` as `SERIAL_PORT`.
5. Upload.

## Verifying the JSON stream

Open the Serial Monitor at **9600 baud**. You must see exactly one JSON
object per line, e.g.:

```json
{"v":1,"uptime_ms":4021,"temperature":24.6,"humidity":51.2,"light":430,"motion":false,"status":"ok","simulated":false}
```

No labels, no prose. If the DHT is disconnected you will see `null` values
plus `{"v":1,"status":"sensor_error","sensor":"dht","code":"READ_FAILED"}`.

**Close the Serial Monitor before starting the backend** — only one program
can own a COM port at a time.

## Firmware simulation mode

With no sensors wired, set in `config.h`:

```c
#define SIMULATION_MODE 1
```

The board then emits plausible synthetic frames tagged `"simulated": true`.
This is distinct from the backend's `AEGIS_MODE=mock` (which needs no board
at all).

## Command testing from the Serial Monitor

Line ending set to *Newline*, then type:

```json
{"action":"request_status"}
{"action":"activate_warning","duration_seconds":3}
{"action":"display_message","message":"Hello"}
{"action":"anything_else"}
```

The last one must answer `{"v":1,"status":"command_rejected","code":"UNKNOWN_ACTION"}`
and produce no hardware effect.

## Component test sketches

`tests/01_led_test` … `tests/06_lcd_test` are stand-alone sketches for
verifying each part in isolation before flashing the integrated firmware.
They print human-readable text and are **not** protocol-compliant — use them
only with the Serial Monitor, never with the backend collector.

## What must be re-verified on real hardware

- Actual sensor models and their tolerances (DHT11 vs DHT22 changes ranges
  and read timing).
- Pin wiring vs `config.h`.
- The real COM port (`SERIAL_PORT`).
- Alert thresholds (`HIGH_TEMPERATURE_C`, `DARK_LIGHT_LEVEL`, …) — tune from
  observed values, they are development defaults.
- PIR warm-up time (many modules need ~60 s after power-on).
- LCD contrast (potentiometer) and wiring.
