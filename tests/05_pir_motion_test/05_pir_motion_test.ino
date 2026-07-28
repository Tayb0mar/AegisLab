const int PIR_PIN = 3;

int previousState = LOW;

void setup() {
  Serial.begin(9600);
  pinMode(PIR_PIN, INPUT);

  Serial.println("PIR motion sensor test started.");
  Serial.println("Allow the sensor time to stabilise.");
}

void loop() {
  int currentState = digitalRead(PIR_PIN);

  if (currentState != previousState) {
    if (currentState == HIGH) {
      Serial.println("MOTION DETECTED");
    } else {
      Serial.println("NO MOTION");
    }

    previousState = currentState;
  }

  delay(100);
}
