import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional, Iterable, List, Tuple


class PlantDBWrapper:
    """
    Per-thread SQLite connections via threading.local().

    - Use self._get_conn() internally for all DB ops.
    - Public .connection() returns the calling thread's connection.
    """

    def __init__(self, path: str, db_schema: str) -> None:
        schema_path = Path(db_schema)
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")
        self._schema_sql = schema_path.read_text(encoding="utf-8")

        target_path = Path(path)
        self.path: Path = target_path
        self._path_str = str(target_path)

        # thread-local holder
        self._local = threading.local()

        # initialize DB file (create/verify schema) using a temporary bootstrap connection
        if not target_path.exists():
            conn = self.__create_with_schema(self._path_str)
            conn.close()
        else:
            if not self.__schemas_match(self._path_str):
                _ = self.__rename_as_backup(target_path)
                conn = self.__create_with_schema(self._path_str)
                conn.close()

    # ------------------- connection utilities -------------------

    def __new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path_str,
            isolation_level=None,      # autocommit
            check_same_thread=False,   # future-proof even if a conn crosses threads
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=3000;")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self.__new_conn()
            self._local.conn = conn
        return conn

    def __create_with_schema(self, path_str: str) -> sqlite3.Connection:
        conn = sqlite3.connect(
            path_str, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=3000;")
        try:
            conn.executescript(self._schema_sql)
        except Exception as e:
            print("Schema execution failed:", e)
            conn.close()
            raise
        return conn

    # ------------------- schema verification -------------------

    def __snapshot(self, conn: sqlite3.Connection):
        rows = conn.execute("""
            SELECT type, name, sql
            FROM sqlite_master
            WHERE sql IS NOT NULL
              AND type IN ('table','index','trigger','view')
        """).fetchall()
        return {(r[0], r[1]): r[2] for r in rows}

    def __schemas_match(self, db_path_str: str) -> bool:
        mem = sqlite3.connect(":memory:")
        try:
            mem.executescript(self._schema_sql)
            expected = self.__snapshot(mem)
        finally:
            mem.close()

        disk = sqlite3.connect(db_path_str)
        try:
            actual = self.__snapshot(disk)
        finally:
            disk.close()

        return expected == actual

    def __rename_as_backup(self, base: Path) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = base.with_name(f"{base.stem}_backup_{ts}{base.suffix}")
        base.rename(backup_path)
        return backup_path

    # ------------------- small helpers -------------------

    def table_exists(self, name: str) -> bool:
        cur = self._get_conn().execute(
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

        row = {
            "ts":             ts,
            "probe_id":       self.__opt_int(payload.get("probe_id")),
            "lux":            self.__opt_float(payload.get("lux")),
            "rh":             self.__opt_float(payload.get("rh")),
            "temp_c":         self.__opt_float(payload.get("temp_c")),
            "moisture_raw":   self.__opt_int(payload.get("moisture_raw")),
            "seq":            self.__opt_int(payload.get("seq")),
            "calibration_id": self.__opt_int(payload.get("calibration_id")),
        }
        if not any(row[k] is not None for k in ("lux", "rh", "temp_c", "moisture_raw")):
            raise ValueError("At least one measurement must be present.")
        return row

    # ------------------- public: inserts -------------------

    def insert_single_reading(self, payload: Dict[str, Any]) -> bool:
        if not self.table_exists("readings"):
            return False
        try:
            row = self.__build_row(payload)
            self._get_conn().execute("""
                INSERT INTO readings (ts, probe_id, lux, rh, temp_c, moisture_raw, seq, calibration_id)
                VALUES (:ts, :probe_id, :lux, :rh, :temp_c, :moisture_raw, :seq, :calibration_id)
            """, row)
            return True
        except Exception as e:
            print(f"Error inserting reading: {e}")
            return False

    def insert_batch_readings(self, payloads: Iterable[Dict[str, Any]]) -> bool:
        if not self.table_exists("readings"):
            return False
        try:
            rows = [self.__build_row(p) for p in payloads]
            if not rows:
                return True
            conn = self._get_conn()
            conn.execute("BEGIN")
            conn.executemany(
                """
                INSERT INTO readings (ts, probe_id, lux, rh, temp_c, moisture_raw, seq, calibration_id)
                VALUES (:ts, :probe_id, :lux, :rh, :temp_c, :moisture_raw, :seq, :calibration_id)
                """,
                rows,
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            try:
                self._get_conn().execute("ROLLBACK")
            finally:
                return False

    def insert_alert(self, probe_id: int, alert_type: str, message: str) -> bool:
        if not self.table_exists("probe_alerts"):
            return False
        try:
            self._get_conn().execute("""
                INSERT INTO probe_alerts (probe_id, type, timestamp, message)
                VALUES (?, ?, strftime('%Y-%m-%d %H:%M:%S', 'now'), ?)
            """, (probe_id, alert_type, message))
            return True
        except Exception as e:
            print(f"Error inserting alert: {e}")
            return False

    # ------------------- calibrations -------------------

    def ensure_probe_exists(self, probe_id: int, label: Optional[str] = None) -> None:
        if not self.table_exists("probes"):
            raise RuntimeError("probes table missing")
        self._get_conn().execute(
            """
            INSERT INTO probes (id, label, is_active)
            VALUES (?, COALESCE(?, 'Probe ' || ?), 1)
            ON CONFLICT(id) DO NOTHING
            """,
            (probe_id, label, probe_id),
        )

    def get_active_calibration_id(self, probe_id: int) -> Optional[int]:
        row = self._get_conn().execute(
            "SELECT id FROM probe_calibrations WHERE probe_id=? AND active=1",
            (probe_id,),
        ).fetchone()
        return int(row["id"]) if row else None

    def set_active_calibration(
        self,
        probe_id: int,
        raw_dry: int,
        raw_wet: int,
        lux_min: float,
        lux_max: float,
        rh_min: float,
        rh_max: float,
        temp_min: float,
        temp_max: float,
        notes: Optional[str] = None,
    ) -> Optional[int]:
        if not self.table_exists("probe_calibrations"):
            return None
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            self.ensure_probe_exists(probe_id)

            conn.execute(
                "UPDATE probe_calibrations SET active=0 WHERE probe_id=? AND active=1",
                (probe_id,),
            )

            cur = conn.execute(
                """
                INSERT INTO probe_calibrations (
                    probe_id, raw_dry, raw_wet,
                    lux_min, lux_max, rh_min, rh_max,
                    temp_min, temp_max, notes, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    probe_id, raw_dry, raw_wet,
                    lux_min, lux_max, rh_min, rh_max,
                    temp_min, temp_max, notes,
                ),
            )
            cal_id = int(cur.lastrowid)

            row = conn.execute(
                "SELECT id FROM probe_calibrations WHERE id=? AND probe_id=? AND active=1",
                (cal_id, probe_id),
            ).fetchone()
            if not row:
                raise RuntimeError("Inserted calibration not visible as active")

            conn.execute("COMMIT")
            return cal_id
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            finally:
                print(f"Error setting active calibration: {e}")
                return None

    def upsert_active_calibration_from_defaults(self, probe_id: int, defaults: Dict[str, Any]) -> Optional[int]:
        cal_id = self.get_active_calibration_id(probe_id)
        if cal_id is not None:
            return cal_id
        return self.set_active_calibration(
            probe_id=probe_id,
            raw_dry=int(defaults["raw_dry"]),
            raw_wet=int(defaults["raw_wet"]),
            lux_min=float(defaults["lux_min"]),
            lux_max=float(defaults["lux_max"]),
            rh_min=float(defaults["rh_min"]),
            rh_max=float(defaults["rh_max"]),
            temp_min=float(defaults["temp_min"]),
            temp_max=float(defaults["temp_max"]),
            notes=defaults.get("notes"),
        )

    def get_validation_envelope(self, probe_id: int) -> Optional[Tuple[int, int, float, float, float, float, float, float]]:
        row = self._get_conn().execute(
            """
            SELECT raw_dry, raw_wet, lux_min, lux_max, rh_min, rh_max, temp_min, temp_max
            FROM probe_calibrations
            WHERE probe_id=? AND active=1
            """,
            (probe_id,),
        ).fetchone()
        if not row:
            return None
        return (row["raw_dry"], row["raw_wet"], row["lux_min"], row["lux_max"],
                row["rh_min"], row["rh_max"], row["temp_min"], row["temp_max"])

    # ------------------- public: reads / health -------------------

    def get_last_readings(self, n: int, oldest_first: bool = False) -> List[Dict[str, Any]]:
        if not self.table_exists("readings"):
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
        cur = self._get_conn().execute(sql, (n,))
        return [dict(row) for row in cur.fetchall()]

    def get_probe_calibration(self, probe_id: int) -> Optional[Dict[str, float]]:
        query = """
        SELECT lux_min, lux_max, rh_min, rh_max, temp_min, temp_max
        FROM probe_calibrations
        WHERE probe_id = ? AND active = 1
        """
        calibration = self._get_conn().execute(query, (probe_id,)).fetchone()

        if calibration:
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
        if not self.table_exists("probe_alerts"):
            return []
        query = """
            SELECT id, probe_id, type, timestamp, message
            FROM probe_alerts
            WHERE probe_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        cur = self._get_conn().execute(query, (probe_id, limit))
        return [dict(row) for row in cur.fetchall()]

    def get_probe_alert_thresholds(self, probe_id: int) -> Optional[Dict[str, Any]]:
        if not self.table_exists("probe_alert_thresholds"):
            return None
        query = """
            SELECT moisture_min, moisture_max, lux_min, lux_max, temp_min, temp_max, rh_min, rh_max
            FROM probe_alert_thresholds
            WHERE probe_id = ?
        """
        cur = self._get_conn().execute(query, (probe_id,))
        result = cur.fetchone()
        if result:
            return dict(result)
        return None

    def health_check(self) -> bool:
        try:
            if not self.table_exists("readings"):
                return False
            row = self._get_conn().execute("PRAGMA quick_check").fetchone()
            return bool(row and row[0] == "ok")
        except Exception:
            return False

    def latest_timestamp(self) -> Optional[str]:
        if not self.table_exists("readings"):
            return None
        row = self._get_conn().execute("SELECT MAX(ts) FROM readings").fetchone()
        return row[0] if row and row[0] is not None else None

    def updated_within(self, seconds: int) -> bool:
        if seconds <= 0 or not self.table_exists("readings"):
            return False
        try:
            row = self._get_conn().execute(
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
        if not self.__is_valid_iso_ts(ts) or not self.table_exists("readings"):
            return False
        try:
            row = self._get_conn().execute(
                "SELECT EXISTS(SELECT 1 FROM readings WHERE ts > ? LIMIT 1)", (ts,)
            ).fetchone()
            return bool(row and row[0] == 1)
        except Exception:
            return False

    def row_count(self) -> int:
        if not self.table_exists("readings"):
            return 0
        row = self._get_conn().execute("SELECT COUNT(*) FROM readings").fetchone()
        return int(row[0]) if row else 0

    # ------------------- convenience -------------------

    def connection(self) -> sqlite3.Connection:
        """Return this thread's live sqlite3.Connection."""
        return self._get_conn()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
