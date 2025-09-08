import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional, Iterable, List


class PlantDBWrapper:
    def __init__(self, path: str, db_schema: str) -> None:
        # load schema file
        schema_path = Path(db_schema)
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")
        self._schema_sql = schema_path.read_text(encoding="utf-8")

        target_path = Path(path)
        self.path: Path
        self.conn: sqlite3.Connection

        if not target_path.exists():
            # brand new DB
            self.path = target_path
            self.conn = self.__create_with_schema(self.path)
            return

        # existing DB -> exact schema match check
        if self.__schemas_match(target_path):
            self.path = target_path
            self.conn = self.__connect(self.path)
        else:
            # mismatch -> move old aside, recreate clean at original name
            _ = self.__rename_as_backup(target_path)
            self.path = target_path
            self.conn = self.__create_with_schema(self.path)

    # --------------- connection utilities ---------------

    def __connect(self, p: Path) -> sqlite3.Connection:
        # autocommit; row dicts; FK checks on
        conn = sqlite3.connect(str(p), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def __create_with_schema(self, p: Path) -> sqlite3.Connection:
        conn = self.__connect(p)
        conn.executescript(self._schema_sql)
        return conn

    # --------------- schema verification ---------------

    def __snapshot(self, conn: sqlite3.Connection):
        rows = conn.execute("""
            SELECT type, name, sql
            FROM sqlite_master
            WHERE sql IS NOT NULL
              AND type IN ('table','index','trigger','view')
        """).fetchall()
        return {(r[0], r[1]): r[2] for r in rows}

    def __schemas_match(self, db_path: Path) -> bool:
        # expected schema in-memory
        mem = sqlite3.connect(":memory:")
        mem.executescript(self._schema_sql)
        expected = self.__snapshot(mem)
        mem.close()

        # actual on-disk
        conn = sqlite3.connect(str(db_path))
        actual = self.__snapshot(conn)
        conn.close()

        return expected == actual

    def __rename_as_backup(self, base: Path) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = base.with_name(f"{base.stem}_backup_{ts}{base.suffix}")
        base.rename(backup_path)
        return backup_path

    # --------------- small helpers ---------------

    def __table_exists(self, name: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
        )
        return cur.fetchone() is not None

    def __is_valid_iso_ts(self, ts: Any) -> bool:
        if not isinstance(ts, str):
            return False
        try:
            datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            return True
        except ValueError:
            return False

    def __opt_float(self, v: Any) -> Optional[float]:
        if v is None: return None
        try: return float(v)
        except (TypeError, ValueError): raise ValueError(f"Expected float, got {v!r}")

    def __opt_int(self, v: Any) -> Optional[int]:
        if v is None: return None
        try: return int(v)
        except (TypeError, ValueError): raise ValueError(f"Expected int, got {v!r}")

    def __opt_str(self, v: Any) -> Optional[str]:
        if v is None: return None
        return str(v)

    def __build_row(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ts = payload.get("ts")
        if not self.__is_valid_iso_ts(ts):
            raise ValueError(f"Invalid timestamp format: {ts!r}")

        # Build the row, now excluding 'err' and 'moisture_pct'
        row = {
            "ts":            ts,
            "probe_id":      self.__opt_str(payload.get("probe_id")),  # Updated to "probe_id"
            "lux":           self.__opt_float(payload.get("lux")),
            "rh":            self.__opt_float(payload.get("rh")),
            "temp_c":        self.__opt_float(payload.get("temp_c")),
            "moisture_raw":  self.__opt_int(payload.get("moisture_raw")),
            "seq":           self.__opt_int(payload.get("seq")),
            "calibration_id": self.__opt_int(payload.get("calibration_id")),  # Calibration ID if available
        }

        if not any(row[k] is not None for k in ("lux", "rh", "temp_c", "moisture_raw")):
            raise ValueError("At least one measurement must be present.")
        return row



    # --------------- public: inserts ---------------

    def insert_single_reading(self, payload: Dict[str, Any]) -> bool:
        if not self.__table_exists("readings"):
            return False
        try:
            # Build the row from the payload
            row = self.__build_row(payload)

            # Insert the reading into the 'readings' table
            self.conn.execute("""
                INSERT INTO readings (ts, probe_id, lux, rh, temp_c, moisture_raw, seq, calibration_id)
                VALUES (:ts, :probe_id, :lux, :rh, :temp_c, :moisture_raw, :seq, :calibration_id)
            """, row)

            return True  # autocommit persists
        except Exception as e:
            print(f"Error inserting reading: {e}")
            return False



    def insert_batch_readings(self, payloads: Iterable[Dict[str, Any]]) -> bool:
        if not self.__table_exists("readings"):
            return False
        try:
            rows = [self.__build_row(p) for p in payloads]
            if not rows:
                return True
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """
                INSERT INTO readings (ts, probe_id, lux, rh, temp_c, moisture_raw, seq, calibration_id)
                VALUES (:ts, :probe_id, :lux, :rh, :temp_c, :moisture_raw, :seq, :calibration_id)
                """,
                rows,
            )
            self.conn.execute("COMMIT")
            return True
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            finally:
                return False
    

    def insert_alert(self, probe_id: int, alert_type: str, message: str) -> bool:
        if not self.__table_exists("probe_alerts"):
            return False
        try:
            # Insert new alert
            self.conn.execute("""
                INSERT INTO probe_alerts (probe_id, type, timestamp, message)
                VALUES (?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now'), ?)
            """, (probe_id, alert_type, message))
            return True
        except Exception as e:
            print(f"Error inserting alert: {e}")
            return False

    def set_active_calibration(self, probe_id: int, raw_dry: int, raw_wet: int, notes: Optional[str] = None) -> bool:
        if not self.__table_exists("probe_calibrations"):
            return False

        try:
            # Deactivate the previous active calibration for the probe (if any)
            self.conn.execute("""
                UPDATE probe_calibrations
                SET active = 0
                WHERE probe_id = ? AND active = 1
            """, (probe_id,))

            # Insert the new calibration and set it as active
            self.conn.execute("""
                INSERT INTO probe_calibrations (probe_id, raw_dry, raw_wet, notes, active)
                VALUES (?, ?, ?, ?, 1)
            """, (probe_id, raw_dry, raw_wet, notes))

            return True
        except Exception as e:
            print(f"Error setting active calibration: {e}")
            return False

    def set_probe_alert_thresholds(
        self, probe_id: int, moisture_min: Optional[int], moisture_max: Optional[int],
        lux_min: Optional[float], lux_max: Optional[float],
        temp_min: Optional[float], temp_max: Optional[float],
        rh_min: Optional[float], rh_max: Optional[float]
    ) -> bool:
        if not self.__table_exists("probe_alert_thresholds"):
            return False
        try:
            # Insert or update the threshold settings for the probe
            self.conn.execute("""
                INSERT INTO probe_alert_thresholds (probe_id, moisture_min, moisture_max, lux_min, lux_max, temp_min, temp_max, rh_min, rh_max)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(probe_id) DO UPDATE SET
                    moisture_min = COALESCE(EXCLUDED.moisture_min, moisture_min),
                    moisture_max = COALESCE(EXCLUDED.moisture_max, moisture_max),
                    lux_min = COALESCE(EXCLUDED.lux_min, lux_min),
                    lux_max = COALESCE(EXCLUDED.lux_max, lux_max),
                    temp_min = COALESCE(EXCLUDED.temp_min, temp_min),
                    temp_max = COALESCE(EXCLUDED.temp_max, temp_max),
                    rh_min = COALESCE(EXCLUDED.rh_min, rh_min),
                    rh_max = COALESCE(EXCLUDED.rh_max, rh_max)
            """, (probe_id, moisture_min, moisture_max, lux_min, lux_max, temp_min, temp_max, rh_min, rh_max))
            return True
        except Exception as e:
            print(f"Error inserting/updating alert thresholds: {e}")
            return False



    # --------------- public: reads / health ---------------

    def get_last_readings(self, n: int, oldest_first: bool = False) -> List[Dict[str, Any]]:
        if not self.__table_exists("readings"):
            return []
        if oldest_first:
            sql = """
                SELECT * FROM (
                    SELECT id, ts, probe_id, lux, rh, temp_c, moisture_raw, moisture_pct, seq
                    FROM readings
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                ) sub
                ORDER BY ts ASC, id ASC
            """
        else:
            sql = """
                SELECT id, ts, probe_id, lux, rh, temp_c, moisture_raw, moisture_pct, seq
                FROM readings
                ORDER BY ts DESC, id DESC
                LIMIT ?
            """
        cur = self.conn.execute(sql, (n,))
        return [dict(row) for row in cur.fetchall()]
    
    def get_probe_calibration(self, probe_id: int) -> Optional[Dict[str, float]]:
        """
        Retrieve the calibration data for a probe, including lux, humidity, and temperature ranges.
        """
        query = """
        SELECT lux_min, lux_max, rh_min, rh_max, temp_min, temp_max
        FROM probe_calibrations
        WHERE probe_id = ? AND active = 1
        """
        calibration = self.conn.execute(query, (probe_id,)).fetchone()

        if calibration:
            # Return the calibration values as a dictionary
            return {
                "lux_min": calibration["lux_min"],
                "lux_max": calibration["lux_max"],
                "rh_min": calibration["rh_min"],
                "rh_max": calibration["rh_max"],
                "temp_min": calibration["temp_min"],
                "temp_max": calibration["temp_max"]
            }
        else:
            print(f"No active calibration found for probe {probe_id}.")
            return None
    
    def get_probe_alerts(self, probe_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        if not self.__table_exists("probe_alerts"):
            return []
        query = """
            SELECT id, probe_id, type, timestamp, message
            FROM probe_alerts
            WHERE probe_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        cur = self.conn.execute(query, (probe_id, limit))
        return [dict(row) for row in cur.fetchall()]

    def get_probe_alert_thresholds(self, probe_id: int) -> Optional[Dict[str, Any]]:
        if not self.__table_exists("probe_alert_thresholds"):
            return None
        query = """
            SELECT moisture_min, moisture_max, lux_min, lux_max, temp_min, temp_max, rh_min, rh_max
            FROM probe_alert_thresholds
            WHERE probe_id = ?
        """
        cur = self.conn.execute(query, (probe_id,))
        result = cur.fetchone()
        if result:
            return dict(result)
        return None


    def health_check(self) -> bool:
        try:
            if not self.__table_exists("readings"):
                return False
            row = self.conn.execute("PRAGMA quick_check").fetchone()
            return bool(row and row[0] == "ok")
        except Exception:
            return False

    def latest_timestamp(self) -> Optional[str]:
        if not self.__table_exists("readings"):
            return None
        row = self.conn.execute("SELECT MAX(ts) FROM readings").fetchone()
        return row[0] if row and row[0] is not None else None

    def updated_within(self, seconds: int) -> bool:
        if seconds <= 0 or not self.__table_exists("readings"):
            return False
        try:
            # Keep timestamp format consistent with __is_valid_iso_ts
            row = self.conn.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM readings
                    WHERE ts >= strftime('%Y-%m-%d %H:%M:%S', 'now', ?)
                    LIMIT 1
                )
                """,
                (f'-{int(seconds)} seconds',),
            ).fetchone()
            return bool(row and row[0] == 1)
        except Exception:
            return False

    def has_updates_since(self, ts: str) -> bool:
        if not self.__is_valid_iso_ts(ts) or not self.__table_exists("readings"):
            return False
        try:
            row = self.conn.execute(
                "SELECT EXISTS(SELECT 1 FROM readings WHERE ts > ? LIMIT 1)", (ts,)
            ).fetchone()
            return bool(row and row[0] == 1)
        except Exception:
            return False

    def row_count(self) -> int:
        if not self.__table_exists("readings"):
            return 0
        row = self.conn.execute("SELECT COUNT(*) FROM readings").fetchone()
        return int(row[0]) if row else 0

    # --------------- convenience ---------------

    def connection(self) -> sqlite3.Connection:
        """Get the live sqlite3.Connection (for advanced/one-off SQL)."""
        return self.conn

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
