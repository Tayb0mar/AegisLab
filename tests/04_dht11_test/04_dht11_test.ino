#include <DHT.h>

const int DHT_PIN = 2;
const int DHT_TYPE = DHT11;

DHT dht(DHT_PIN, DHT_TYPE);

void setup() {
  Serial.begin(9600);
  dht.begin();

  Serial.println("DHT11 test started.");
}

void loop() {
  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("ERROR: Could not read the DHT11 sensor.");
  } else {
    Serial.print("Temperature: ");
    Serial.print(temperature);
    Serial.print(" C | Humidity: ");
    Serial.print(humidity);
    Serial.println(" %");
  }

  delay(2000);
}
