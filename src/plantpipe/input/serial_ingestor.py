
#!/usr/bin/env python3
"""
Serial JSON → SQLite ingestor for Arduino plant sensor.

Usage:
  python serial_ingestor.py --port /dev/ttyUSB0 --baud 115200 --db plant.db
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone

try:
    import serial  # pyserial
except ImportError as e:
    print("ERROR: pyserial is required. Install with: pip install pyserial", file=sys.stderr)
    raise

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,                 -- ISO 8601 timestamp (UTC)
    plant_id INTEGER NOT NULL,
    seq INTEGER,
    lux REAL,
    rh REAL,
    temp REAL,
    moisture_raw INTEGER NOT NULL,
    moisture_pct REAL,
    err TEXT,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts_utc);
CREATE INDEX IF NOT EXISTS idx_readings_plant_ts ON readings (plant_id, ts_utc);
CREATE INDEX IF NOT EXISTS idx_readings_plant_seq ON readings (plant_id, seq);
"""

EXPECTED_KEYS = {
    "plant_id": int,
    "seq": int,
    "lux": (int, float, type(None)),
    "rh": (int, float, type(None)),
    "temp": (int, float, type(None)),
    "moisture_raw": int,
    "moisture_pct": (int, float),
    "err": str,
}

def ensure_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    conn.commit()

def parse_args():
    ap = argparse.ArgumentParser(description="Ingest newline-delimited JSON from a serial port into SQLite.")
    ap.add_argument("--port", required=True, help="Serial port path, e.g., /dev/ttyUSB0 or COM3")
    ap.add_argument("--baud", type=int, default=115200, help="Serial baud rate (default: 115200)")
    ap.add_argument("--db", required=True, help="SQLite database file path (will be created if missing)")
    ap.add_argument("--table", default="readings", help="SQLite table name (default: readings)")
    ap.add_argument("--print", action="store_true", help="Echo parsed rows to stdout")
    ap.add_argument("--flush-every", type=int, default=25, help="Commit every N rows (default: 25)")
    ap.add_argument("--timeout", type=float, default=2.5, help="Serial read timeout in seconds (default: 2.5)")
    ap.add_argument("--max-retries", type=int, default=0, help="Retries on serial open failure (default: 0 = no retries)")
    return ap.parse_args()

def open_serial(port, baud, timeout, max_retries):
    attempt = 0
    while True:
        try:
            return serial.Serial(port=port, baudrate=baud, timeout=timeout)
        except Exception as e:
            attempt += 1
            if attempt > max_retries:
                raise
            sleep_s = min(5 * attempt, 30)
            print(f"[warn] Serial open failed ({e!r}); retrying in {sleep_s}s...", file=sys.stderr)
            time.sleep(sleep_s)

def validate_payload(obj: dict):
    # Minimal shape check; don't be pedantic—store raw JSON too.
    for k, typ in EXPECTED_KEYS.items():
        if k not in obj:
            raise ValueError(f"Missing key: {k}")
        if not isinstance(obj[k], typ):
            # Allow numeric nullables: treat None as None for optional numeric fields
            if obj[k] is None and type(None) in (typ if isinstance(typ, tuple) else (typ,)):
                continue
            raise ValueError(f"Bad type for '{k}': {type(obj[k]).__name__}")
    return True

def row_from_payload(obj: dict):
    # Map JSON → tuple matching table schema
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        now_iso,
        int(obj["plant_id"]),
        int(obj.get("seq")) if obj.get("seq") is not None else None,
        float(obj["lux"]) if obj.get("lux") is not None else None,
        float(obj["rh"]) if obj.get("rh") is not None else None,
        float(obj["temp"]) if obj.get("temp") is not None else None,
        int(obj["moisture_raw"]),
        float(obj["moisture_pct"]) if obj.get("moisture_pct") is not None else None,
        str(obj.get("err", "")),
        json.dumps(obj, separators=(",", ":"), ensure_ascii=False),
    )

def upsert_reading(conn: sqlite3.Connection, table: str, row: tuple):
    # Insert; a natural unique key could be (plant_id, seq) if seq is monotonic per plant.
    # We'll avoid ON CONFLICT by default to keep simple; add a UNIQUE if you want dedup.
    conn.execute(
        f"""INSERT INTO {table}
            (ts_utc, plant_id, seq, lux, rh, temp, moisture_raw, moisture_pct, err, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )

def main():
    args = parse_args()
    conn = sqlite3.connect(args.db)
    ensure_schema(conn)

    # Verify the target table exists or create if not (allows --table override).
    if args.table != "readings":
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {args.table} AS SELECT * FROM readings WHERE 0""")
        # ensure indexes on custom table
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{args.table}_ts ON {args.table} (ts_utc)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{args.table}_plant_ts ON {args.table} (plant_id, ts_utc)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{args.table}_plant_seq ON {args.table} (plant_id, seq)")
        conn.commit()

    ser = open_serial(args.port, args.baud, args.timeout, args.max_retries)
    print(f"[info] Connected {ser.port} @ {ser.baudrate}; writing to {args.db}:{args.table}", file=sys.stderr)

    rows_since_commit = 0
    try:
        while True:
            chunk = ser.readline()  # reads until \n or timeout
            if not chunk:
                continue
            line = chunk.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
                validate_payload(obj)
                row = row_from_payload(obj)
            except Exception as e:
                # Store as error row in a simple dead-letter table for later analysis
                conn.execute("""CREATE TABLE IF NOT EXISTS bad_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT NOT NULL,
                    line TEXT NOT NULL,
                    error TEXT NOT NULL
                )""")
                conn.execute(
                    "INSERT INTO bad_rows (ts_utc, line, error) VALUES (?, ?, ?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), line, repr(e)),
                )
                conn.commit()
                print(f"[bad] {e}: {line}", file=sys.stderr)
                continue

            upsert_reading(conn, args.table, row)
            rows_since_commit += 1

            if args.__dict__.get("print"):
                # Compact single-line print
                print(f"{row[0]} plant={row[1]} seq={row[2]} lux={row[3]} rh={row[4]} temp={row[5]} mraw={row[6]} mpct={row[7]} err='{row[8]}'")

            if rows_since_commit >= args.flush_every:
                conn.commit()
                rows_since_commit = 0

    except KeyboardInterrupt:
        print("\n[info] Ctrl-C received, flushing…", file=sys.stderr)
    finally:
        try:
            conn.commit()
        except Exception:
            pass
        conn.close()
        try:
            ser.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()




