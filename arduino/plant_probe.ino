









#include <BH1750.h>
#include <Wire.h>
#include "TinyDHT.h"

#define DHTPIN 2
#define DHTTYPE DHT22
#define MOISTURE_PIN A0

DHT dht(DHTPIN, DHTTYPE);
BH1750 lightMeter;


const uint8_t PLANT_ID = 1;
uint32_t seq = 0;

const int MOISTURE_RAW_DRY = 450;
const int MOISTURE_RAW_WET = 190;

float moisture_pct_from_raw(int raw) {
  int lo = min(MOISTURE_RAW_DRY, MOISTURE_RAW_WET);
  int hi = max(MOISTURE_RAW_DRY, MOISTURE_RAW_WET);
  if (raw < lo) raw = lo;
  if (raw > hi) raw = hi;
  float span = float(hi - lo);
  float pos = float(raw - lo);

  bool wetReadsLower = (MOISTURE_RAW_WET < MOISTURE_RAW_DRY);
  float pct = wetReadsLower ? (1.0f - pos / span) * 100.0f
                            : (pos / span) * 100.0f;
  return pct;
}


void setup() {
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(100000); 
  dht.begin();
  lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE);

  delay(1000);
}


void loop() {
  String err = "";

  float lux = lightMeter.readLightLevel();
  if (lux < 0 || lux > 300000) {
    err += (err.length() ? "|" : "");
    err += "BH1750";
    lux = NAN;
  }
  
  int8_t rh = dht.readHumidity();
  int16_t temp = dht.readTemperature();
  if (isnan(rh) || isnan(temp) || rh < 0 || rh > 100 || temp < -20 || temp > 70) {
    err += (err.length() ? "|" : "");
    err += "DHT";
    rh = NAN; temp = NAN;
  }

  int moistureRaw = analogRead(MOISTURE_PIN);
  delay(5);
  moistureRaw = (moistureRaw + analogRead(MOISTURE_PIN)) / 2;
  float moisturePct = moisture_pct_from_raw(moistureRaw);


  Serial.print("{\"plant_id\":");
  Serial.print(PLANT_ID);

  Serial.print(",\"seq\":");
  Serial.print(seq++);

  Serial.print(",\"lux\":");
  if (isnan(lux)) Serial.print("null"); else Serial.print(lux, 1);

  Serial.print(",\"rh\":");
  if (isnan(rh)) Serial.print("null"); else Serial.print(rh, 1);

  Serial.print(",\"temp\":");
  if (isnan(temp)) Serial.print("null"); else Serial.print(temp, 1);

  Serial.print(",\"moisture_raw\":");
  Serial.print(moistureRaw);

  Serial.print(",\"moisture_pct\":");
  Serial.print(moisturePct, 1);

  Serial.print(",\"err\":\"");
  Serial.print(err);
  Serial.println("\"}");


  delay(2000);
}
