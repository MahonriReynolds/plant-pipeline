-- PRAGMA foreign_keys = ON;         -- enforce foreign keys on each connection
-- PRAGMA journal_mode = WAL;        -- use Write-Ahead Logging for improved performance

BEGIN;

-- ---------- Probes Table (link to the physical probe) ----------
CREATE TABLE IF NOT EXISTS probes (
  id          INTEGER PRIMARY KEY,         -- Probe ID
  label       TEXT NOT NULL,               -- e.g., "Monstera #1"
  notes       TEXT,                        -- optional probe-specific notes
  is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),  -- active flag
  created_at  TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                CHECK (
                  created_at = strftime('%Y-%m-%d %H:%M:%S', created_at)
                  AND datetime(created_at) IS NOT NULL
                )
) STRICT;

-- Enforce unique, case-insensitive labels (trim spaces)
CREATE UNIQUE INDEX IF NOT EXISTS ux_probes_label_ci
  ON probes(LOWER(TRIM(label)));

-- ---------- Probe Calibrations (versioned) ----------
CREATE TABLE IF NOT EXISTS probe_calibrations (
  id          INTEGER PRIMARY KEY,
  probe_id    INTEGER NOT NULL
                REFERENCES probes(id) ON DELETE CASCADE ON UPDATE RESTRICT, -- foreign key for probe
  raw_dry     INTEGER NOT NULL CHECK (raw_dry BETWEEN 0 AND 1023),
  raw_wet     INTEGER NOT NULL CHECK (raw_wet BETWEEN 0 AND 1023),
  lux_min     REAL NOT NULL CHECK (lux_min >= 0),  -- Minimum Lux value for the probe
  lux_max     REAL NOT NULL CHECK (lux_max >= 0),  -- Maximum Lux value for the probe
  rh_min      REAL NOT NULL CHECK (rh_min >= 0 AND rh_min <= 100), -- Minimum RH value (0-100%)
  rh_max      REAL NOT NULL CHECK (rh_max >= 0 AND rh_max <= 100), -- Maximum RH value (0-100%)
  temp_min    REAL NOT NULL CHECK (temp_min >= -40 AND temp_min <= 85), -- Minimum Temperature (°C)
  temp_max    REAL NOT NULL CHECK (temp_max >= -40 AND temp_max <= 85), -- Maximum Temperature (°C)
  notes       TEXT,
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)), -- active flag for current calibration
  created_at  TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                CHECK (
                  created_at = strftime('%Y-%m-%d %H:%M:%S', created_at)
                  AND datetime(created_at) IS NOT NULL
                )
) STRICT;

-- Only one active calibration per probe
CREATE UNIQUE INDEX IF NOT EXISTS ux_cal_active_per_probe
  ON probe_calibrations(probe_id) WHERE active = 1;

CREATE INDEX IF NOT EXISTS idx_cal_probe_created
  ON probe_calibrations(probe_id, created_at);

-- ---------- Readings (store data from probes) ----------
CREATE TABLE IF NOT EXISTS readings (
  id             INTEGER PRIMARY KEY,
  ts             TEXT NOT NULL
                  DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                  CHECK (
                    ts = strftime('%Y-%m-%d %H:%M:%S', ts)
                    AND datetime(ts) IS NOT NULL
                  ),  -- ISO 8601
  probe_id       INTEGER NOT NULL
                 REFERENCES probes(id) ON DELETE RESTRICT ON UPDATE RESTRICT,  -- link to probe

  lux            REAL CHECK (lux IS NULL OR (lux >= 0 AND lux <= 300000)),   -- Lux readings
  rh             REAL CHECK (rh  IS NULL OR (rh  >= 0 AND rh  <= 100)),     -- Relative Humidity
  temp_c         REAL CHECK (temp_c IS NULL OR (temp_c > -40 AND temp_c < 85)), -- Temperature
  moisture_raw   INTEGER CHECK (moisture_raw IS NULL OR (moisture_raw BETWEEN 0 AND 1023)),  -- Moisture sensor raw value
  moisture_pct   REAL,   -- Moisture percentage will be calculated by the server based on calibration

  seq            INTEGER CHECK (seq IS NULL OR seq >= 0),  -- optional monotonic sequence
  calibration_id INTEGER
                 REFERENCES probe_calibrations(id) ON DELETE SET NULL ON UPDATE RESTRICT, -- calibration used for this reading
  err            TEXT,  -- error message (if any) - not used in Arduino anymore, but kept for logging
  err_flags      INTEGER CHECK (err_flags IS NULL OR err_flags >= 0), -- bitmask for error states
  fw             TEXT CHECK (fw IS NULL OR length(fw) <= 64),  -- firmware version
  uptime_ms      INTEGER CHECK (uptime_ms IS NULL OR uptime_ms >= 0)  -- uptime of the device
) STRICT;

-- Indexes for fast querying
CREATE INDEX IF NOT EXISTS idx_readings_ts        ON readings(ts);
CREATE INDEX IF NOT EXISTS idx_readings_probe_ts  ON readings(probe_id, ts);
CREATE INDEX IF NOT EXISTS idx_readings_calib     ON readings(calibration_id);

-- Prevent duplicates for same (probe_id, ts, seq).
CREATE UNIQUE INDEX IF NOT EXISTS ux_readings_probe_ts_seq
  ON readings(probe_id, ts, seq);

-- ---------- Trigger to automatically calculate moisture_pct after insert into readings ----------
CREATE TRIGGER IF NOT EXISTS update_moisture_pct_after_insert
AFTER INSERT ON readings
FOR EACH ROW
BEGIN
    UPDATE readings
    SET moisture_pct =
        CASE
            WHEN NEW.moisture_raw IS NULL OR NEW.calibration_id IS NULL THEN NULL
            ELSE (
                SELECT
                    CASE
                        WHEN raw_dry = raw_wet THEN NULL
                        ELSE
                            -- 0% at raw_dry, 100% at raw_wet, clamp to [0,100]
                            CAST(
                              MIN(100, MAX(0,
                                ROUND( (100.0 * (raw_dry - NEW.moisture_raw)) / (raw_dry - raw_wet) )
                              ))
                            AS INTEGER)
                    END
                FROM probe_calibrations
                WHERE id = NEW.calibration_id
            )
        END
    WHERE id = NEW.id;
END;

-- ---------- Probe Alert Thresholds ----------
CREATE TABLE IF NOT EXISTS probe_alert_thresholds (
    probe_id INTEGER PRIMARY KEY,                         -- Link to probe ID
    moisture_min INTEGER CHECK (moisture_min >= 0 AND moisture_min <= 1023),  -- Min moisture threshold (raw scale)
    moisture_max INTEGER CHECK (moisture_max >= 0 AND moisture_max <= 1023),  -- Max moisture threshold (raw scale)
    lux_min REAL CHECK (lux_min >= 0),                   -- Min lux threshold
    lux_max REAL CHECK (lux_max >= 0),                   -- Max lux threshold
    temp_min REAL CHECK (temp_min >= -40 AND temp_min <= 85),  -- Min temperature in °C
    temp_max REAL CHECK (temp_max >= -40 AND temp_max <= 85),  -- Max temperature in °C
    rh_min REAL CHECK (rh_min >= 0 AND rh_min <= 100),    -- Min humidity in percentage
    rh_max REAL CHECK (rh_max >= 0 AND rh_max <= 100),    -- Max humidity in percentage
    created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                CHECK (
                  created_at = strftime('%Y-%m-%d %H:%M:%S', created_at)
                  AND datetime(created_at) IS NOT NULL
                ),
    updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                CHECK (
                  updated_at = strftime('%Y-%m-%d %H:%M:%S', updated_at)
                  AND datetime(updated_at) IS NOT NULL
                )
) STRICT;

-- ---------- Probe Alerts Table ----------
CREATE TABLE IF NOT EXISTS probe_alerts (
    id INTEGER PRIMARY KEY,                             -- Unique ID for the alert
    probe_id INTEGER NOT NULL,                          -- Link to the probe
    type TEXT NOT NULL CHECK (type IN ('too_dry', 'too_wet', 'lux_out_of_range', 'temp_out_of_range', 'rh_out_of_range', 'lux_avg_out_of_range', 'moisture_avg_out_of_range')),
    timestamp TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                CHECK (
                  timestamp = strftime('%Y-%m-%d %H:%M:%S', timestamp)
                  AND datetime(timestamp) IS NOT NULL
                ),
    message TEXT NOT NULL,                              -- Description of the alert
    created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                CHECK (
                  created_at = strftime('%Y-%m-%d %H:%M:%S', created_at)
                  AND datetime(created_at) IS NOT NULL
                ),
    updated_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
                CHECK (
                  updated_at = strftime('%Y-%m-%d %H:%M:%S', updated_at)
                  AND datetime(updated_at) IS NOT NULL
                ),
    FOREIGN KEY(probe_id) REFERENCES probes(id) ON DELETE CASCADE  -- Foreign key to probe table
) STRICT;

COMMIT;
