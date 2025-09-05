-- sql/002_rollups.sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- Materialized 5-minute rollups (UTC)
CREATE TABLE IF NOT EXISTS readings_5m (
  plant_id            INTEGER NOT NULL,
  bucket_start_utc    TEXT    NOT NULL,      -- ISO8601 like 2025-09-04T23:10:00+00:00
  row_count           INTEGER NOT NULL,

  lux_avg             REAL,  lux_min REAL,  lux_max REAL,
  rh_avg              REAL,  rh_min  REAL,  rh_max  REAL,
  temp_avg            REAL,  temp_min REAL, temp_max REAL,
  moisture_pct_avg    REAL,  moisture_pct_min REAL,  moisture_pct_max REAL,
  moisture_raw_avg    REAL,  moisture_raw_min REAL,  moisture_raw_max REAL,

  err_count           INTEGER NOT NULL DEFAULT 0,
  updated_at          TEXT    NOT NULL,

  PRIMARY KEY (plant_id, bucket_start_utc)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_readings_5m_plant_bucket ON readings_5m (plant_id, bucket_start_utc);

-- Optional: meta row for /health
CREATE TABLE IF NOT EXISTS rollup_meta (
  id                 INTEGER PRIMARY KEY CHECK (id = 1),
  last_run_utc       TEXT,
  last_cutoff_utc    TEXT
) STRICT;

INSERT OR IGNORE INTO rollup_meta (id, last_run_utc, last_cutoff_utc)
VALUES (1, NULL, NULL);
