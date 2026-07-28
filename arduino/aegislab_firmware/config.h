#ifndef AEGISLAB_CONFIG_H
#define AEGISLAB_CONFIG_H

// ---------------------------------------------------------------------------
// AegisLab firmware configuration.
// EVERY hardware-dependent value lives here. The pin numbers below match the
// component test sketches in tests/ (01_led_test .. 06_lcd_test); verify them
// against your actual wiring before flashing.
// ---------------------------------------------------------------------------

// Set to 1 to emit plausible synthetic values without any sensor attached.
// Simulated frames carry "simulated": true so the backend can label them.
#define SIMULATION_MODE 0

// --- Serial ---------------------------------------------------------------
#define SERIAL_BAUD_RATE 9600UL
#define PROTOCOL_VERSION 1

// --- Sampling -------------------------------------------------------------
#define READING_INTERVAL_MS 2000UL

// --- DHT temperature/humidity sensor --------------------------------------
// Requires the Adafruit "DHT sensor library" (used by tests/04_dht11_test).
// Change DHT_SENSOR_TYPE to DHT22 if your module differs.
#define DHT_PIN 2
#define DHT_SENSOR_TYPE DHT11

// --- Other sensors ---------------------------------------------------------
#define LIGHT_SENSOR_PIN A0   // photoresistor divider, 10-bit ADC (0..1023)
#define PIR_PIN 3             // PIR motion sensor digital output

// --- Outputs ----------------------------------------------------------------
#define LED_PIN 22
#define BUZZER_PIN 23
#define BUZZER_TONE_HZ 1000

// LCD 16x2 in 4-bit parallel mode (LiquidCrystal library, built into the IDE)
#define LCD_RS 7
#define LCD_ENABLE 8
#define LCD_D4 9
#define LCD_D5 10
#define LCD_D6 11
#define LCD_D7 12
#define LCD_COLS 16
#define LCD_ROWS 2

// --- Local alert behaviour --------------------------------------------------
// The firmware raises its own local warning (LED + bounded buzzer) when the
// measured temperature reaches this value. Backend thresholds are separate.
#define LOCAL_HIGH_TEMP_C 30.0

// Buzzer bounding: even while a warning is active the buzzer beeps in bursts
// and never sounds longer than BUZZER_MAX_CONTINUOUS_MS in one stretch.
#define BUZZER_BEEP_ON_MS 200UL
#define BUZZER_BEEP_OFF_MS 800UL
#define BUZZER_MAX_CONTINUOUS_MS 500UL

// --- Command handling -------------------------------------------------------
#define COMMAND_BUFFER_SIZE 96
#define COMMAND_MIN_DURATION_S 1
#define COMMAND_MAX_DURATION_S 10
#define DISPLAY_MESSAGE_MAX_LEN 16      // one LCD line
#define DISPLAY_MESSAGE_HOLD_MS 5000UL  // how long a display_message stays up

#endif  // AEGISLAB_CONFIG_H
