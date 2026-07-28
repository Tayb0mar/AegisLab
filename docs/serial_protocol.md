# AegisLab — Serial Protocol Contract (v1)

Transport: USB serial, 9600 baud, UTF-8 text, **exactly one JSON object per
line**, `\n` terminated. No prose, labels or debug text is ever mixed into
the stream in production firmware. All frames include `"v": 1` (protocol
version).

## Device → backend

### Reading frame (every `READING_INTERVAL_MS`, default 2000 ms)

```json
{"v":1,"uptime_ms":123456,"temperature":24.6,"humidity":51.2,"light":430,"motion":false,"status":"ok","simulated":false}
```

| Field | Type | Rules |
|---|---|---|
| `v` | int | Protocol version, currently 1 |
| `uptime_ms` | int ≥ 0 | Milliseconds since firmware boot |
| `temperature` | number \| null | °C; null when the sensor read failed |
| `humidity` | number \| null | 0–100 %; null on failure |
| `light` | int \| null | 0–1023 (10-bit ADC); null on failure |
| `motion` | bool | JSON `true`/`false` only |
| `status` | string | `"ok"` on reading frames |
| `simulated` | bool | `true` when generated without real sensors |

Values are always finite JSON numbers — never `NaN`/`Infinity`. A failed
sensor produces `null` **plus** a separate error frame; the firmware never
fabricates a numeric value.

The collector ignores unknown extra keys on reading frames (documented
policy), so the firmware may add fields without breaking older backends.

### Sensor error frame

```json
{"v":1,"status":"sensor_error","sensor":"dht","code":"READ_FAILED"}
```

Known `sensor` values: `dht`, `light`. Known codes: `READ_FAILED`,
`OUT_OF_RANGE`.

### Command responses

```json
{"v":1,"status":"command_ack","action":"activate_warning"}
{"v":1,"status":"command_rejected","code":"UNKNOWN_ACTION"}
{"v":1,"status":"device_status","uptime_ms":123456,"warning_active":false,"simulated":false}
```

Rejection codes: `NOT_JSON`, `MISSING_ACTION`, `UNKNOWN_ACTION`,
`INVALID_DURATION`, `INVALID_MESSAGE`, `LINE_TOO_LONG`.

## Backend → device (commands)

One JSON object per line, ASCII, canonical form produced only by
`backend/command_validator.py`. The audit `reason` field is validated
backend-side and **never transmitted**.

| Action | Extra fields | Bounds |
|---|---|---|
| `activate_warning` | `duration_seconds` | integer 1–10 |
| `deactivate_warning` | — | — |
| `display_message` | `message` | 1–16 printable ASCII chars |
| `request_status` | — | — |

Examples on the wire:

```json
{"action":"activate_warning","duration_seconds":3}
{"action":"display_message","message":"Hello lab"}
```

Anything else — unknown action, out-of-bounds duration, non-printable or
over-long message, extra keys, oversized line (> 95 chars) — is rejected by
the backend before transmission, and rejected again by the firmware if it
somehow arrives.

## Validation at the backend boundary

A reading is accepted only when: the line decodes as UTF-8; parses as JSON;
the root is an object; `temperature`, `humidity`, `light`, `motion` all
exist; numeric fields are numbers (booleans explicitly rejected) and finite;
temperature is within `TEMPERATURE_MIN_C..TEMPERATURE_MAX_C`; humidity is
0–100; light is an integer within `LIGHT_MIN..LIGHT_MAX`; motion is a JSON
boolean. Anything else is rejected, counted and rate-limited-logged; the
collector never crashes on bad input.
