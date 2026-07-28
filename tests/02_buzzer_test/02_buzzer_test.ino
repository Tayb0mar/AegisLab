const int BUZZER_PIN = 23;

void setup() {
  pinMode(BUZZER_PIN, OUTPUT);
}

void loop() {
  tone(BUZZER_PIN, 1000);
  delay(500);

  noTone(BUZZER_PIN);
  delay(1500);
}
