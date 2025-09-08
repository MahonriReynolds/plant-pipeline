#include <BH1750.h>
#include <Wire.h>
#include "TinyDHT.h"

#define DHTPIN 2
#define DHTTYPE DHT22
#define MOISTURE_PIN A0

DHT dht(DHTPIN, DHTTYPE);
BH1750 lightMeter;

// Probe-related constants
const uint8_t PROBE_ID = 1;
uint32_t seq = 0;

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(100000); 
  dht.begin();
  lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);

  delay(1000);
}

void loop() {
  // Read light level from BH1750
  float lux = lightMeter.readLightLevel();

  // Read temperature and humidity from DHT22
  int8_t rh = dht.readHumidity();
  int16_t temp = dht.readTemperature();

  // Read moisture sensor
  int moistureRaw = analogRead(MOISTURE_PIN);
  delay(5);  // Small delay to stabilize sensor reading (it;s analog)
  moistureRaw = (moistureRaw + analogRead(MOISTURE_PIN)) / 2; // Averaging two samples

  // Output the sensor data as JSON (no moisture_pct sent, only moisture_raw)
  Serial.print("{\"probe_id\":");
  Serial.print(PROBE_ID);  // Use 'probe_id' instead of 'plant_id'

  Serial.print(",\"seq\":");
  Serial.print(seq++);

  Serial.print(",\"lux\":");
  Serial.print(lux, 1);  // Light level (lux)

  Serial.print(",\"rh\":");
  Serial.print(rh, 1);  // Humidity percentage

  Serial.print(",\"temp\":");
  Serial.print(temp, 1);  // Temperature in Celsius

  Serial.print(",\"moisture_raw\":");
  Serial.print(moistureRaw);  // Raw moisture sensor value (only)

  Serial.println("}");  // Closing the JSON object

  delay(2000);  // Delay before next reading (2 seconds)
}
