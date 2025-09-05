
import json
from datetime import datetime, timezone
import serial
from typing import Dict


class ProbeReader:
    def __init__(self, port: str, baud: int, timeout: int=2.5) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout

        self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
    
    # intake a single line
    def read_single(self):
        raw = self.ser.readline()
        if not raw:
            return None

        try:
            line = json.loads(raw.decode("utf-8", "replace").strip())
        except json.JSONDecodeError:
            return None

        # helper float check
        def as_float(v):
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        # helper int check
        def as_int(v):
            if v is None:
                return None
            try:
                # handle floats that are really ints, and numeric strings
                return int(float(v))
            except (TypeError, ValueError):
                return None

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "ts": ts,
            "plant": str(line.get("plant_id", "-")),
            "lux": as_float(line.get("lux")),
            "rh": as_float(line.get("rh")),
            "temp_c": as_float(line.get("temp")),
            "moisture_raw": as_int(line.get("moisture_raw")),
            "moisture_pct": as_float(line.get("moisture_pct")),
            "seq": as_int(line.get("seq")),
            "err": "" if line.get("err") is None else str(line.get("err")),
        }

    
    # constant output 
    def stream(self):
        while True:
            rec = self.read_single()
            if rec is not None:
                yield rec
    
    # cleanly close connection
    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        
    # enable use in for loops
    def __iter__(self):
        return self.stream()




