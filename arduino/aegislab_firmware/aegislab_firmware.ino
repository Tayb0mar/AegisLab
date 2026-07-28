// ---------------------------------------------------------------------------
// AegisLab firmware — Arduino Mega 2560
//
// Emits exactly one JSON object per serial line (newline terminated) and
// never mixes human prose into the stream. Reading frame:
//   {"v":1,"uptime_ms":123456,"temperature":24.6,"humidity":51.2,
//    "light":430,"motion":false,"status":"ok","simulated":false}
// Failed sensors produce null values plus a separate error frame:
//   {"v":1,"status":"sensor_error","sensor":"dht","code":"READ_FAILED"}
//
// Incoming commands are newline-terminated JSON validated against a fixed
// allowlist; anything else is answered with a command_rejected frame and
// never touches an output pin. Hardware configuration lives in config.h.
// ---------------------------------------------------------------------------

#include <DHT.h>
#include <LiquidCrystal.h>

#include "config.h"

DHT dht(DHT_PIN, DHT_SENSOR_TYPE);
LiquidCrystal lcd(LCD_RS, LCD_ENABLE, LCD_D4, LCD_D5, LCD_D6, LCD_D7);

// --- state ------------------------------------------------------------------

unsigned long lastReadingAt = 0;

// Warning output state (local rule or backend command).
bool warningActive = false;
unsigned long warningUntil = 0;   // 0 = no timed expiry (local rule keeps it)
bool warningFromCommand = false;

// Buzzer beep pattern state (bounded: bursts, never continuous).
bool buzzerOn = false;
unsigned long buzzerPhaseStart = 0;

// Temporary LCD message from display_message command.
char displayMessage[DISPLAY_MESSAGE_MAX_LEN + 1] = "";
unsigned long displayMessageUntil = 0;

// Serial command receive buffer.
char commandBuffer[COMMAND_BUFFER_SIZE];
size_t commandLength = 0;
bool commandOverflow = false;

// Last known sensor values for the LCD.
float lastTemperature = NAN;
float lastHumidity = NAN;
int lastLight = -1;
bool lastMotion = false;

#if SIMULATION_MODE
unsigned long simTick = 0;
#endif

// --- JSON output helpers ----------------------------------------------------
// All output goes through these helpers so the stream stays valid JSON lines.

void printJsonNumberOrNull(float value, unsigned char decimals) {
  if (isnan(value) || isinf(value)) {
    Serial.print(F("null"));
  } else {
    Serial.print(value, decimals);
  }
}

void emitReading(float temperature, float humidity, int light, bool motion) {
  Serial.print(F("{\"v\":"));
  Serial.print(PROTOCOL_VERSION);
  Serial.print(F(",\"uptime_ms\":"));
  Serial.print(millis());
  Serial.print(F(",\"temperature\":"));
  printJsonNumberOrNull(temperature, 1);
  Serial.print(F(",\"humidity\":"));
  printJsonNumberOrNull(humidity, 1);
  Serial.print(F(",\"light\":"));
  if (light < 0) {
    Serial.print(F("null"));
  } else {
    Serial.print(light);
  }
  Serial.print(F(",\"motion\":"));
  Serial.print(motion ? F("true") : F("false"));
  Serial.print(F(",\"status\":\"ok\",\"simulated\":"));
#if SIMULATION_MODE
  Serial.println(F("true}"));
#else
  Serial.println(F("false}"));
#endif
}

void emitSensorError(const __FlashStringHelper *sensor,
                     const __FlashStringHelper *code) {
  Serial.print(F("{\"v\":"));
  Serial.print(PROTOCOL_VERSION);
  Serial.print(F(",\"status\":\"sensor_error\",\"sensor\":\""));
  Serial.print(sensor);
  Serial.print(F("\",\"code\":\""));
  Serial.print(code);
  Serial.println(F("\"}"));
}

void emitCommandAck(const char *action) {
  Serial.print(F("{\"v\":"));
  Serial.print(PROTOCOL_VERSION);
  Serial.print(F(",\"status\":\"command_ack\",\"action\":\""));
  Serial.print(action);
  Serial.println(F("\"}"));
}

void emitCommandRejected(const __FlashStringHelper *code) {
  Serial.print(F("{\"v\":"));
  Serial.print(PROTOCOL_VERSION);
  Serial.print(F(",\"status\":\"command_rejected\",\"code\":\""));
  Serial.print(code);
  Serial.println(F("\"}"));
}

void emitDeviceStatus() {
  Serial.print(F("{\"v\":"));
  Serial.print(PROTOCOL_VERSION);
  Serial.print(F(",\"status\":\"device_status\",\"uptime_ms\":"));
  Serial.print(millis());
  Serial.print(F(",\"warning_active\":"));
  Serial.print(warningActive ? F("true") : F("false"));
  Serial.print(F(",\"simulated\":"));
#if SIMULATION_MODE
  Serial.println(F("true}"));
#else
  Serial.println(F("false}"));
#endif
}

// --- sensor acquisition -----------------------------------------------------

void acquireSensors(float &temperature, float &humidity, int &light,
                    bool &motion) {
#if SIMULATION_MODE
  // Plausible synthetic values: slow triangle drift plus PIR-like pulses.
  simTick++;
  float phase = (float)(simTick % 120);
  temperature = 22.0 + phase * 0.05;
  humidity = 45.0 + phase * 0.1;
  light = 400 + (int)(phase * 3.0);
  motion = (simTick % 30) < 2;
#else
  temperature = dht.readTemperature();
  humidity = dht.readHumidity();
  if (isnan(temperature) || isnan(humidity)) {
    emitSensorError(F("dht"), F("READ_FAILED"));
  }

  light = analogRead(LIGHT_SENSOR_PIN);
  if (light < 0 || light > 1023) {
    emitSensorError(F("light"), F("OUT_OF_RANGE"));
    light = -1;  // emitted as null
  }

  motion = digitalRead(PIR_PIN) == HIGH;
#endif
}

// --- warning outputs (LED + bounded buzzer) ---------------------------------

void startWarning(unsigned long durationMs, bool fromCommand) {
  warningActive = true;
  warningFromCommand = fromCommand;
  warningUntil = (durationMs == 0) ? 0 : millis() + durationMs;
  buzzerPhaseStart = millis();
  buzzerOn = false;
}

void stopWarning() {
  warningActive = false;
  warningFromCommand = false;
  warningUntil = 0;
  noTone(BUZZER_PIN);
  buzzerOn = false;
  digitalWrite(LED_PIN, LOW);
}

void updateWarningOutputs() {
  unsigned long now = millis();

  if (warningActive && warningUntil != 0 && now >= warningUntil) {
    stopWarning();
  }

  if (!warningActive) {
    digitalWrite(LED_PIN, LOW);
    noTone(BUZZER_PIN);
    return;
  }

  digitalWrite(LED_PIN, HIGH);

  // Bounded beep pattern: BUZZER_BEEP_ON_MS on, BUZZER_BEEP_OFF_MS off.
  if (buzzerOn) {
    unsigned long cap = min(BUZZER_BEEP_ON_MS, BUZZER_MAX_CONTINUOUS_MS);
    if (now - buzzerPhaseStart >= cap) {
      noTone(BUZZER_PIN);
      buzzerOn = false;
      buzzerPhaseStart = now;
    }
  } else {
    if (now - buzzerPhaseStart >= BUZZER_BEEP_OFF_MS) {
      tone(BUZZER_PIN, BUZZER_TONE_HZ);
      buzzerOn = true;
      buzzerPhaseStart = now;
    }
  }
}

// --- LCD --------------------------------------------------------------------

void updateLcd() {
  lcd.clear();

  if (displayMessage[0] != '\0' && millis() < displayMessageUntil) {
    lcd.setCursor(0, 0);
    lcd.print(displayMessage);
    lcd.setCursor(0, 1);
    lcd.print(warningActive ? F("!! WARNING !!") : F("AegisLab"));
    return;
  }

  lcd.setCursor(0, 0);
  if (isnan(lastTemperature) || isnan(lastHumidity)) {
    lcd.print(F("SENSOR ERROR"));
  } else {
    lcd.print(F("T:"));
    lcd.print(lastTemperature, 1);
    lcd.print(F(" H:"));
    lcd.print(lastHumidity, 0);
    lcd.print(F("%"));
  }

  lcd.setCursor(0, 1);
  if (warningActive) {
    lcd.print(F("!! WARNING !!"));
  } else {
    lcd.print(F("L:"));
    if (lastLight < 0) {
      lcd.print(F("--"));
    } else {
      lcd.print(lastLight);
    }
    lcd.print(F(" M:"));
    lcd.print(lastMotion ? F("Y") : F("N"));
#if SIMULATION_MODE
    lcd.print(F(" SIM"));
#endif
  }
}

// --- command parsing --------------------------------------------------------
// Minimal, strict extraction for the fixed command schema. The backend only
// ever sends the canonical form produced by its own validator, but the
// firmware still enforces the allowlist and bounds on its own (defence in
// depth). Anything unexpected is rejected.

bool extractStringField(const char *json, const char *key, char *out,
                        size_t outSize) {
  char pattern[24];
  snprintf(pattern, sizeof(pattern), "\"%s\":\"", key);
  const char *start = strstr(json, pattern);
  if (start == NULL) {
    return false;
  }
  start += strlen(pattern);
  size_t i = 0;
  while (start[i] != '\0' && start[i] != '"' && i < outSize - 1) {
    char c = start[i];
    // Reject escapes and non-printable characters outright: the canonical
    // backend serialisation never produces them for allowlisted commands.
    if (c == '\\' || c < 0x20 || c > 0x7E) {
      return false;
    }
    out[i] = c;
    i++;
  }
  if (start[i] != '"') {
    return false;  // unterminated or too long
  }
  out[i] = '\0';
  return true;
}

bool extractIntField(const char *json, const char *key, long *out) {
  char pattern[28];
  snprintf(pattern, sizeof(pattern), "\"%s\":", key);
  const char *start = strstr(json, pattern);
  if (start == NULL) {
    return false;
  }
  start += strlen(pattern);
  char *end = NULL;
  long value = strtol(start, &end, 10);
  if (end == start) {
    return false;
  }
  *out = value;
  return true;
}

void handleCommandLine(const char *line) {
  // Must at least look like a JSON object.
  if (line[0] != '{') {
    emitCommandRejected(F("NOT_JSON"));
    return;
  }

  char action[24];
  if (!extractStringField(line, "action", action, sizeof(action))) {
    emitCommandRejected(F("MISSING_ACTION"));
    return;
  }

  if (strcmp(action, "activate_warning") == 0) {
    long duration = 0;
    if (!extractIntField(line, "duration_seconds", &duration) ||
        duration < COMMAND_MIN_DURATION_S ||
        duration > COMMAND_MAX_DURATION_S) {
      emitCommandRejected(F("INVALID_DURATION"));
      return;
    }
    startWarning((unsigned long)duration * 1000UL, true);
    emitCommandAck(action);
  } else if (strcmp(action, "deactivate_warning") == 0) {
    stopWarning();
    emitCommandAck(action);
  } else if (strcmp(action, "display_message") == 0) {
    char message[DISPLAY_MESSAGE_MAX_LEN + 1];
    if (!extractStringField(line, "message", message, sizeof(message)) ||
        message[0] == '\0') {
      emitCommandRejected(F("INVALID_MESSAGE"));
      return;
    }
    strncpy(displayMessage, message, sizeof(displayMessage) - 1);
    displayMessage[sizeof(displayMessage) - 1] = '\0';
    displayMessageUntil = millis() + DISPLAY_MESSAGE_HOLD_MS;
    updateLcd();
    emitCommandAck(action);
  } else if (strcmp(action, "request_status") == 0) {
    emitDeviceStatus();
  } else {
    emitCommandRejected(F("UNKNOWN_ACTION"));
  }
}

void pollSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (commandOverflow) {
        emitCommandRejected(F("LINE_TOO_LONG"));
      } else if (commandLength > 0) {
        commandBuffer[commandLength] = '\0';
        handleCommandLine(commandBuffer);
      }
      commandLength = 0;
      commandOverflow = false;
    } else if (commandLength < COMMAND_BUFFER_SIZE - 1) {
      commandBuffer[commandLength++] = c;
    } else {
      commandOverflow = true;
    }
  }
}

// --- main -------------------------------------------------------------------

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);

  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(PIR_PIN, INPUT);
  digitalWrite(LED_PIN, LOW);

  dht.begin();
  lcd.begin(LCD_COLS, LCD_ROWS);
  lcd.print(F("AegisLab boot"));
}

void loop() {
  unsigned long now = millis();

  pollSerialCommands();
  updateWarningOutputs();

  if (now - lastReadingAt >= READING_INTERVAL_MS) {
    lastReadingAt = now;

    float temperature = NAN;
    float humidity = NAN;
    int light = -1;
    bool motion = false;
    acquireSensors(temperature, humidity, light, motion);

    lastTemperature = temperature;
    lastHumidity = humidity;
    lastLight = light;
    lastMotion = motion;

    emitReading(temperature, humidity, light, motion);

    // Local rule: warn on high temperature (independent of the backend).
    if (!isnan(temperature) && temperature >= LOCAL_HIGH_TEMP_C) {
      if (!warningActive) {
        startWarning(0, false);  // stays on while the condition holds
      }
    } else if (warningActive && !warningFromCommand && warningUntil == 0) {
      stopWarning();
    }

    updateLcd();
  }
}
