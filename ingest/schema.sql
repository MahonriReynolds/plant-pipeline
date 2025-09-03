PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS plant(
  plant_id INTEGER PRIMARY KEY,
  name TEXT DEFAULT 'dieffenbachia',
  species TEXT
);


INSERT OR IGNORE INTO plant(plant_id,name) VALUES(1, 'dieffenbachia');

CREATE TABLE IF NOT EXISTS reading(
  id INTEGER PRIMARY KEY,
  plant_id INTEGER NOT NULL,
  received_ts_utc TEXT NOT NULL, --ISO8601
  seq INTEGER,
  lux REAL,
  rh REAL,
  temp REAL,
  moisture_raw INTEGER,
  moisture_pct REAL,
  quality TEXT DEFAULT 'ok', -- ok / bad
  err TEXT,
  FOREIGN KEY(plant_id) REFERENCES plant(plant_id)
);

CREATE INDEX IF NOT EXISTS idx_reading_ts ON reading(received_ts_utc);
