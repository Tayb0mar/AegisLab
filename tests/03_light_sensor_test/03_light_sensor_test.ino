const int LIGHT_PIN = A0;

void setup() {
  Serial.begin(9600);
  Serial.println("Light sensor test started.");
}

void loop() {
  int lightLevel = analogRead(LIGHT_PIN);

  Serial.print("Light level: ");
  Serial.println(lightLevel);

  delay(500);
}
