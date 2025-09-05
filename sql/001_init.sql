PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- Raw readings
CREATE TABLE IF NOT EXISTS readings (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc           TEXT    NOT NULL,                                -- ISO8601 UTC
  plant_id         INTEGER NOT NULL,
  seq              INTEGER,                                         -- may reset; used only for short-term dedup in ingestor
  lux              REAL,                                            -- nullable
  rh               REAL,                                            -- nullable
  temp             REAL,                                            -- nullable
  moisture_raw     INTEGER NOT NULL,
  moisture_pct     REAL,                                            -- nullable
  err              TEXT,                                            -- nullable short code(s)
  raw_json         TEXT    NOT NULL,
  -- Sanity rails (nullable fields must allow NULL):
  CHECK ( (rh   IS NULL) OR (rh   BETWEEN 0 AND 100) ),
  CHECK ( (temp IS NULL) OR (temp BETWEEN -40 AND 85) ),            -- DHT22 typical bounds
  CHECK ( (lux  IS NULL) OR (lux  >= 0) ),
  CHECK ( moisture_raw >= 0 ),
  CHECK ( (moisture_pct IS NULL) OR (moisture_pct BETWEEN 0 AND 100) ),
  -- Prevent pathological double-inserts at the same stamped time for a plant
  UNIQUE (plant_id, ts_utc)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_readings_ts           ON readings (ts_utc);
CREATE INDEX IF NOT EXISTS idx_readings_plant_ts     ON readings (plant_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_readings_plant_seq    ON readings (plant_id, seq);

-- 5-minute materialized rollups (one row per plant per bucket)
CREATE TABLE IF NOT EXISTS readings_5m (
  plant_id           INTEGER NOT NULL,
  bucket_start_utc   TEXT    NOT NULL,    -- inclusive, ISO8601 UTC; bucket = [start, start+5m)
  count              INTEGER NOT NULL,
  lux_avg            REAL,  lux_min REAL,  lux_max REAL,
  rh_avg             REAL,   rh_min  REAL, rh_max  REAL,
  temp_avg           REAL,   temp_min REAL,temp_max REAL,
  moisture_avg       REAL,   moisture_min REAL, moisture_max REAL,
  err_count          INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (plant_id, bucket_start_utc)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_readings5m_plant_time ON readings_5m (plant_id, bucket_start_utc);

-- Current alert snapshot (fast read for the card)
CREATE TABLE IF NOT EXISTS alert_state (
  plant_id            INTEGER PRIMARY KEY,
  updated_utc         TEXT    NOT NULL,

  -- Booleans as ints (0/1) + last values that caused a change
  lux_out_of_range    INTEGER NOT NULL DEFAULT 0,
  rh_out_of_range     INTEGER NOT NULL DEFAULT 0,
  temp_out_of_range   INTEGER NOT NULL DEFAULT 0,

  moisture_low_now    INTEGER NOT NULL DEFAULT 0,  -- absolute min breach
  moisture_low_sustain INTEGER NOT NULL DEFAULT 0, -- under min for T minutes
  moisture_high_sustain INTEGER NOT NULL DEFAULT 0,

  last_values_json    TEXT    NOT NULL             -- compact JSON snapshot for UI tooltips
) STRICT;

-- Optional history (keep if you want to review opens/clears)
CREATE TABLE IF NOT EXISTS alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  plant_id      INTEGER NOT NULL,
  type          TEXT    NOT NULL,        -- e.g., 'lux_range','moisture_low_sustain'
  status        TEXT    NOT NULL,        -- 'open' | 'cleared'
  at_utc        TEXT    NOT NULL,
  value         REAL,
  note          TEXT
) STRICT;

-- Keep a place for failed lines (ingestor already uses similar)
CREATE TABLE IF NOT EXISTS bad_rows (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc TEXT NOT NULL,
  line   TEXT NOT NULL,
  error  TEXT NOT NULL
) STRICT;
