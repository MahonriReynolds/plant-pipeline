

"""
Plant Sensor Ingestion Test Setup
=================================

This setup simulates an Arduino sensor board streaming JSON over serial,
and ingests the data into an SQLite database.

Components
----------
1. serial_ingestor.py
   - Listens on a serial port.
   - Reads one JSON object per line.
   - Validates and stores each row in SQLite (default table: readings).

2. fake_arduino.py
   - Acts like the Arduino.
   - Opens the "other side" of a pseudo-TTY pair.
   - Periodically writes JSON with oscillating sensor values.

Usage
-----
# 1. Create a pseudo-terminal pair (Linux/macOS):
    socat -d -d pty,raw,echo=0 pty,raw,echo=0
    # Example output:
    #   PTY is /dev/pts/3
    #   PTY is /dev/pts/4

# 2. Start the ingestor on one end:
    python serial_ingestor.py --port /dev/pts/3 --db data/plant.db --print

# 3. Start the fake Arduino on the other:
    python arduino_mimic.py --port /dev/pts/4 --baud 115200 --interval 2 --probe-id 1

# 4. Observe console output (if --print enabled) and inspect DB:
    sqlite3 plant.db "SELECT * FROM readings LIMIT 5;"

Notes
-----
- Replace /dev/pts/X with your socat output.
- On Windows, you can use com0com or similar tools to create virtual COM ports.
- If you later connect a real Arduino, just point --port at the actual device.
"""


import argparse
import json
import math
import random
import sys
import time

import serial  # pip install pyserial


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="Serial port to write to (the 'other side' of the PTY pair)")
    ap.add_argument("--baud", type=int, default=115200, help="Baud rate (default 115200)")
    ap.add_argument("--interval", type=float, default=2.0, help="Seconds between messages")
    ap.add_argument("--probe-id", type=int, default=1, help="Probe ID to include in output JSON")
    return ap.parse_args()


def main():
    args = parse_args()
    ser = serial.Serial(args.port, args.baud)

    seq = 0
    t0 = time.time()

    try:
        while True:
            elapsed = time.time() - t0

            # Smooth oscillations
            lux = 200 + 100 * math.sin(elapsed / 30.0)          # lux cycles every ~3min
            rh = 50 + 20 * math.sin(elapsed / 60.0)             # humidity slower drift
            temp = 22 + 3 * math.sin(elapsed / 120.0)           # temp very slow cycle
            moisture_raw = 320 + int(30 * math.sin(elapsed / 15.0))

            # Add a little noise
            lux += random.uniform(-5, 5)
            rh += random.uniform(-2, 2)
            temp += random.uniform(-0.5, 0.5)

            # Build ONLY the fields the ingestor reads
            obj = {
                "probe_id": int(args.probe_id),
                "seq": int(seq),
                "lux": round(float(lux), 1),
                "rh": round(float(rh), 1),
                "temp": round(float(temp), 1),        # ingestor maps "temp" -> temp_c
                "moisture_raw": int(moisture_raw),
            }

            line = json.dumps(obj, separators=(",", ":")) + "\n"
            ser.write(line.encode("utf-8"))
            ser.flush()

            if seq % 10 == 0:
                print(f"sent: {line.strip()}", file=sys.stderr)

            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()






