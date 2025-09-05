#!/usr/bin/env python3
"""
Serial JSON → SQLite ingestor for Arduino plant sensor.

Usage:
  python serial_ingestor.py --port /dev/ttyUSB0 --baud 115200 --db data/plant.db --spool data/ingest_spool.ndjson
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import serial  # pyserial
except ImportError as e:
    print("ERROR: pyserial is required. Install with: pip install pyserial", file=sys.stderr)
    raise

# Use the shared DB bootstrap (WAL + schema init)
from plantpipe.storage.database import get_connection, ensure_schema, ensure_rollups

# Spool/backlog knobs
SPOOL_ROTATE_BYTES = 200 * 1024 * 1024  # rotate at 200MB
SPOOL_KEEP_FILES = 5
TIME_COMMIT_MAX_SEC = 2.0

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
    ap.add_argument("--spool", default="data/ingest_spool.ndjson", help="Append raw lines to this NDJSON file (set empty to disable)")
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

def ensure_aux_tables(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bad_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            line TEXT NOT NULL,
            error TEXT NOT NULL
        )
    """)
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_bad_rows_ts ON bad_rows(ts_utc)""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingest_state (
            plant_id INTEGER PRIMARY KEY,
            last_seq INTEGER,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()

def load_last_seq(conn: sqlite3.Connection) -> dict[int, int]:
    state = {}
    for plant_id, last_seq in conn.execute("SELECT plant_id, last_seq FROM ingest_state"):
        if last_seq is not None:
            state[int(plant_id)] = int(last_seq)
    return state

def persist_last_seq(conn: sqlite3.Connection, plant_id: int, seq: Optional[int]) -> None:
    if seq is None:
        return
    conn.execute(
        """
        INSERT INTO ingest_state (plant_id, last_seq, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(plant_id) DO UPDATE SET last_seq=excluded.last_seq, updated_at=excluded.updated_at
        """,
        (plant_id, int(seq), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )

def validate_payload(obj: dict):
    # Minimal shape check; allow None for nullable numeric fields.
    for k, typ in EXPECTED_KEYS.items():
        if k not in obj:
            raise ValueError(f"Missing key: {k}")
        if not isinstance(obj[k], typ):
            if obj[k] is None and (isinstance(typ, tuple) and type(None) in typ):
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
    conn.execute(
        f"""INSERT INTO {table}
            (ts_utc, plant_id, seq, lux, rh, temp, moisture_raw, moisture_pct, err, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )

def spool_offset_path(spool_path: pathlib.Path) -> pathlib.Path:
    return spool_path.with_suffix(spool_path.suffix + ".offset")

def load_spool_offset(spool_path: pathlib.Path) -> int:
    off_path = spool_offset_path(spool_path)
    try:
        return int(off_path.read_text().strip() or "0")
    except Exception:
        return 0

def save_spool_offset(spool_path: pathlib.Path, fp) -> None:
    off_path = spool_offset_path(spool_path)
    pos = fp.tell()
    tmp = off_path.with_suffix(off_path.suffix + ".tmp")
    tmp.write_text(str(pos) + "\n", encoding="utf-8")
    os.replace(tmp, off_path)
    with open(off_path, "r+") as f:
        f.flush()
        os.fsync(f.fileno())

def fsync_spool(fp) -> None:
    try:
        fp.flush()
        os.fsync(fp.fileno())
    except Exception:
        pass

def list_rotations(spool_path: pathlib.Path):
    prefix = spool_path.name + "."
    for p in sorted(spool_path.parent.glob(prefix + "*.ndjson"), key=lambda p: p.stat().st_mtime, reverse=True):
        yield p

def rotate_spool_if_needed(spool_path: pathlib.Path, fp):
    try:
        size = spool_path.stat().st_size
    except FileNotFoundError:
        size = 0
    if size < SPOOL_ROTATE_BYTES:
        return spool_path, fp
    try:
        fp.close()
    except Exception:
        pass
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rotated = spool_path.with_name(spool_path.name + f".{ts}.ndjson")
    try:
        spool_path.rename(rotated)
    except FileNotFoundError:
        pass
    rotations = list(list_rotations(spool_path))
    for old in rotations[SPOOL_KEEP_FILES:]:
        try:
            old.unlink()
        except Exception:
            pass
    new_fp = open(spool_path, "a", buffering=1, encoding="utf-8")
    save_spool_offset(spool_path, new_fp)
    return spool_path, new_fp

def main():
    args = parse_args()

    if args.table != "readings":
        print("[warn] --table override not supported in MVP; using 'readings'", file=sys.stderr)
        args.table = "readings"

    db_path = pathlib.Path(args.db).resolve()
    conn = get_connection(db_path)
    ensure_schema(conn)
    ensure_rollups(conn)
    ensure_aux_tables(conn)

    # Optional spool
    spool_fp = None
    if args.spool:
        spool_path = pathlib.Path(args.spool)
        spool_path.parent.mkdir(parents=True, exist_ok=True)
        spool_fp = open(spool_path, "a", buffering=1, encoding="utf-8")
        _ = load_spool_offset(spool_path)  # reserved for future replayer

    ser = open_serial(args.port, args.baud, args.timeout, args.max_retries)
    print(f"[info] Connected {ser.port} @ {ser.baudrate}; writing to {db_path}:{args.table}", file=sys.stderr)

    # Dedup & counters
    last_seq_by_plant = load_last_seq(conn)
    dropped_dups = 0
    rows_since_commit = 0
    last_commit_time = time.time()

    try:
        while True:
            chunk = ser.readline()
            if not chunk:
                # Quiet idle; you can log a heartbeat here if you want
                continue
            line = chunk.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            # Spool first
            if spool_fp:
                try:
                    print(line, file=spool_fp)
                except Exception as e:
                    print(f"[warn] Failed to write spool line: {e!r}", file=sys.stderr)

            # Parse & validate
            try:
                obj = json.loads(line)
                validate_payload(obj)
            except Exception as e:
                conn.execute(
                    "INSERT INTO bad_rows (ts_utc, line, error) VALUES (?, ?, ?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), line, repr(e)),
                )
                conn.commit()
                print(f"[bad] {e}: {line}", file=sys.stderr)
                continue

            # Immediate duplicate filter
            plant = int(obj["plant_id"])
            seq = obj.get("seq")
            if seq is not None:
                prev = last_seq_by_plant.get(plant)
                if prev is not None and seq == prev:
                    dropped_dups += 1
                    continue
                last_seq_by_plant[plant] = seq

            # Build row and insert
            row = row_from_payload(obj)
            try:
                upsert_reading(conn, args.table, row)
                persist_last_seq(conn, plant, seq)
            except sqlite3.IntegrityError as ie:
                if "CHECK constraint failed" in str(ie):
                    conn.execute(
                        "INSERT INTO bad_rows (ts_utc, line, error) VALUES (?, ?, ?)",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), line, f"CHECK_FAIL: {ie!s}"),
                    )
                    conn.commit()
                    print(f"[bad] CHECK failed: {ie!s}: {line}", file=sys.stderr)
                # Swallow UNIQUE collisions quietly
                continue

            rows_since_commit += 1

            if args.__dict__.get("print"):
                print(
                    f"{row[0]} plant={row[1]} seq={row[2]} lux={row[3]} rh={row[4]} "
                    f"temp={row[5]} mraw={row[6]} mpct={row[7]} err='{row[8]}'"
                )

            # Commit policy: row-count OR time cap
            now = time.time()
            if rows_since_commit >= args.flush_every or (now - last_commit_time) >= TIME_COMMIT_MAX_SEC:
                conn.commit()
                last_commit_time = now
                rows_since_commit = 0
                if spool_fp:
                    fsync_spool(spool_fp)
                    save_spool_offset(spool_path, spool_fp)
                    spool_path, spool_fp = rotate_spool_if_needed(spool_path, spool_fp)

    except KeyboardInterrupt:
        print(f"\n[info] Ctrl-C received, flushing… dropped_dups={dropped_dups}", file=sys.stderr)
    finally:
        try:
            conn.commit()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass
        if spool_fp:
            try:
                fsync_spool(spool_fp)
                save_spool_offset(spool_path, spool_fp)
                spool_fp.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
