-- schema.sql (V1: single sensor)

CREATE TABLE readings (
    id             INTEGER PRIMARY KEY,  -- rowid alias

    -- ISO-8601 UTC timestamp to the second: "YYYY-MM-DD HH:MM:SS"
    ts             TEXT NOT NULL
                   CHECK (
                       length(ts) = 19
                       AND ts GLOB '????-??-?? ??:??:??'
                       AND datetime(ts) IS NOT NULL
                   ),

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
CREATE INDEX idx_readings_ts ON readings(ts);

