-- schema.sql (V1 + unique triple + plant/time index + WAL)

BEGIN;

-- Enable WAL (persists in the DB file); NORMAL is a good durability/speed tradeoff for WAL.
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS readings (
    id             INTEGER PRIMARY KEY,  -- rowid alias

    -- ISO-8601 UTC timestamp to the second: "YYYY-MM-DD HH:MM:SS"
    ts             TEXT NOT NULL
                   CHECK (
                       length(ts) = 19
                       AND ts GLOB '????-??-?? ??:??:??'
                       AND datetime(ts) IS NOT NULL
                   ),

    plant          INTEGER CHECK (plant >= 0),

    -- measurements
    lux            REAL    CHECK (lux >= 0),
    rh             REAL    CHECK (rh BETWEEN 0 AND 100),
    temp_c         REAL    CHECK (temp_c > -100 AND temp_c < 200),
    moisture_raw   INTEGER CHECK (moisture_raw BETWEEN 150 AND 500),
    moisture_pct   REAL    CHECK (moisture_pct BETWEEN 0 AND 100),

    -- metadata
    seq            INTEGER,               -- optional monotonic count
    err            TEXT                   -- optional error/code
) STRICT;

-- fast time queries
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

-- fast plant-over-time queries
CREATE INDEX IF NOT EXISTS idx_readings_plant_ts ON readings(plant, ts);

-- prevent duplicates for the same (plant, ts, seq)
-- Note: rows with NULL seq are allowed to repeat (SQLite treats NULLs as distinct)
CREATE UNIQUE INDEX IF NOT EXISTS ux_readings_plant_ts_seq ON readings(plant, ts, seq);

COMMIT;
