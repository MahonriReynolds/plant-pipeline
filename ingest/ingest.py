import json, time, sqlite3, os, sys
from datetime import datetime, timezone
import serial
from ingest.config import Config

cfg = Config()

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def ensure_db(conn):
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("SELECT 1 FROM plant LIMIT 1")

def quality(row):
    if row["err"]: return "bad"

    sensor_values = {k: v for k, v in row.items() if k not in ("plant_id", "seq", "err")}
    if any(v is None for v in sensor_values.values()): return "bad"

    return "ok"

def insert_row(conn, row):
    conn.execute("""
      INSERT INTO reading(plant_id,received_ts_utc,seq,lux,rh,temp,moisture_raw,moisture_pct,quality,err)
      VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (cfg.plant_id, utcnow(), row["seq"], row["lux"], row["rh"], row["temp"], row["moisture_raw"], row["moisture_pct"],
          quality(row), row["err"] or "")
    )
    conn.commit()

def main():
    ser = serial.Serial(cfg.serial_port, cfg.serial_baud, timeout=2)
    conn = sqlite3.connect(cfg.db_path)
    try:
        ensure_db(conn)
    except Exception as e:
        print("DB not initialized: ", e); sys.exit(1)

    print(f"Listening on {cfg.serial_port} @ {cfg.serial_baud}")
    drops = 0
    while True:
        line = ser.readline().decode('utf-8', errors='ignore').strip()
        if not line:
            time.sleep(0.2); continue

        try:
            data = json.loads(line)
            for k in ("lux", "rh", "temp", "moisture_raw", "moisture_pct"):
                if data[k] is not None and isinstance(data[k], (int, float)):
                    if k=="rh" and not (0 <= data[k] <= 100): data[k] = None
                    if k=="temp" and not (-20 <= data[k] <= 70): data[k] = None
                    if k=="lux" and data[k] < 0: data[k] = None

            insert_row(conn, data)
        except json.JSONDecodeError:
            drops += 1
            if drops % 10 == 1:
                print("Bad JSON (showing first few):", line[:120])
        except Exception as e:
            print("Ingest error:", e)
            time.sleep(0.5)

if __name__ == '__main__':
    main()

