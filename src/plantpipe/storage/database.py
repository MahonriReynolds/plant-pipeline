
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional, Iterable, List


class ReadingsDBWrapper:
    def __init__(self, path: str, db_schema: str):
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

        # existing DB → exact schema match check
        if self.__schemas_match(target_path):
            self.path = target_path
            self.conn = self.__connect(self.path)
        else:
            # mismatch → move old aside, recreate clean at original name
            _ = self.__rename_as_backup(target_path)
            self.path = target_path
            self.conn = self.__create_with_schema(self.path)

    # --------------- connection utilities ---------------

    def __connect(self, p: Path) -> sqlite3.Connection:
        # autocommit; row dicts; FK checks on
        conn = sqlite3.connect(str(p), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
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

        row = {
            "ts":            ts,
            "lux":           self.__opt_float(payload.get("lux")),
            "rh":            self.__opt_float(payload.get("rh")),
            "temp_c":        self.__opt_float(payload.get("temp_c")),
            "moisture_raw":  self.__opt_int(payload.get("moisture_raw")),
            "moisture_pct":  self.__opt_float(payload.get("moisture_pct")),
            "seq":           self.__opt_int(payload.get("seq")),
            "err":           self.__opt_str(payload.get("err")),
        }

        if not any(row[k] is not None for k in ("lux", "rh", "temp_c", "moisture_raw", "moisture_pct")):
            raise ValueError("At least one measurement must be present.")
        return row

    # --------------- public: inserts ---------------

    def insert_single_reading(self, payload: Dict[str, Any]) -> bool:
        if not self.__table_exists("readings"):
            return False
        try:
            row = self.__build_row(payload)  # raises on bad payload
            self.conn.execute(
                """
                INSERT INTO readings
                  (ts, lux, rh, temp_c, moisture_raw, moisture_pct, seq, err)
                VALUES
                  (:ts, :lux, :rh, :temp_c, :moisture_raw, :moisture_pct, :seq, :err)
                """,
                row,
            )
            return True  # autocommit persists
        except Exception:
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
                INSERT INTO readings
                  (ts, lux, rh, temp_c, moisture_raw, moisture_pct, seq, err)
                VALUES
                  (:ts, :lux, :rh, :temp_c, :moisture_raw, :moisture_pct, :seq, :err)
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

    # --------------- public: reads / health ---------------

    def get_last_readings(self, n: int, oldest_first: bool = False) -> List[Dict[str, Any]]:
        if not self.__table_exists("readings"):
            return []
        if oldest_first:
            sql = """
                SELECT * FROM (
                    SELECT id, ts, lux, rh, temp_c, moisture_raw, moisture_pct, seq, err
                    FROM readings
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                ) sub
                ORDER BY ts ASC, id ASC
            """
        else:
            sql = """
                SELECT id, ts, lux, rh, temp_c, moisture_raw, moisture_pct, seq, err
                FROM readings
                ORDER BY ts DESC, id DESC
                LIMIT ?
            """
        cur = self.conn.execute(sql, (n,))
        return [dict(row) for row in cur.fetchall()]

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
            row = self.conn.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM readings
                    WHERE ts >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
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
