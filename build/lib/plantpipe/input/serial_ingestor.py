# src/plantpipe/input/serial_ingestor.py

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import serial
from plantpipe.storage.database import PlantDBWrapper


class ProbeManager:
    """
    Owns calibration lifecycle (DB-backed), validation, and writing readings.
    """

    def __init__(self, db: PlantDBWrapper, defaults: Dict[str, Any]) -> None:
        self.db = db
        self.defaults = defaults
        self._cal_id_cache: Dict[int, int] = {}

    # ---------- calibration ----------

    def get_active_calibration_id(self, probe_id: int) -> Optional[int]:
        cal_id = self._cal_id_cache.get(probe_id)
        if cal_id is not None:
            return cal_id
        cal_id = self.db.get_active_calibration_id(probe_id)
        if cal_id is not None:
            self._cal_id_cache[probe_id] = cal_id
        return cal_id

    def ensure_active_calibration(self, probe_id: int) -> int:
        cal_id = self.get_active_calibration_id(probe_id)
        if cal_id is not None:
            return cal_id
        cal_id = self.db.upsert_active_calibration_from_defaults(probe_id, self.defaults)
        if cal_id is None:
            raise RuntimeError(f"Failed to create default calibration for probe {probe_id}")
        self._cal_id_cache[probe_id] = cal_id
        return cal_id

    def invalidate_calibration_cache(self, probe_id: Optional[int] = None) -> None:
        if probe_id is None:
            self._cal_id_cache.clear()
        else:
            self._cal_id_cache.pop(probe_id, None)

    # ---------- validation ----------

    def validate_sensor_ranges(
        self,
        probe_id: int,
        lux: Optional[float],
        rh: Optional[float],
        temp_c: Optional[float],
        moisture_raw: Optional[int],
    ) -> bool:
        env = self.db.get_validation_envelope(probe_id)
        if not env:
            print(f"No active calibration found for probe {probe_id}. Cannot validate sensor readings.")
            return False

        raw_dry, raw_wet, lux_min, lux_max, rh_min, rh_max, temp_min, temp_max = env

        if lux is not None and not (lux_min <= lux <= lux_max):
            print(f"Lux {lux} out of range [{lux_min}, {lux_max}] for probe {probe_id}")
            return False
        if rh is not None and not (rh_min <= rh <= rh_max):
            print(f"RH {rh} out of range [{rh_min}, {rh_max}] for probe {probe_id}")
            return False
        if temp_c is not None and not (temp_min <= temp_c <= temp_max):
            print(f"Temp {temp_c} out of range [{temp_min}, {temp_max}] for probe {probe_id}")
            return False
        if moisture_raw is not None:
            lo, hi = sorted((raw_dry, raw_wet))
            if not (lo <= moisture_raw <= hi):
                print(f"Moisture raw {moisture_raw} out of range [{lo}, {hi}] for probe {probe_id}")
                return False
        return True

    # ---------- ingest ----------

    def ingest_reading(self, line: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pid = line.get("probe_id", line.get("plant_id"))
        if pid is None:
            print("Skipping record without probe_id/plant_id")
            return None

        try:
            probe_id = int(pid)
        except (TypeError, ValueError):
            print(f"Skipping record with non-integer probe_id: {pid!r}")
            return None

        lux = self._maybe_float(line.get("lux"))
        rh = self._maybe_float(line.get("rh"))
        temp_c = self._maybe_float(line.get("temp"))  # device field name
        moisture_raw = self._maybe_int(line.get("moisture_raw"))
        seq = self._maybe_int(line.get("seq"))

        cal_id = self.ensure_active_calibration(probe_id)

        if not self.validate_sensor_ranges(probe_id, lux, rh, temp_c, moisture_raw):
            print(f"Skipping invalid reading for probe {probe_id}")
            return None

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "ts": ts,
            "probe_id": probe_id,
            "lux": lux,
            "rh": rh,
            "temp_c": temp_c,
            "moisture_raw": moisture_raw,
            "seq": seq,
            "calibration_id": cal_id,
        }

        ok = self.db.insert_single_reading(payload)
        if not ok:
            print(f"Insert failed for probe {probe_id}")
            return None

        return payload

    # ---------- helpers ----------

    @staticmethod
    def _maybe_float(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _maybe_int(v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None




class ProbeReader:
    """
    Thin serial reader: decode JSON and hand off to ProbeManager.
    Manager owns calibration, validation, and DB insert.
    """

    def __init__(self, port: str, baud: int, db_wrapper: PlantDBWrapper, defaults: Dict[str, Any], timeout: float = 2.5) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.db = db_wrapper
        self.manager = ProbeManager(db_wrapper, defaults)
        self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)

    def read_single(self) -> Optional[Dict[str, Any]]:
        raw = self.ser.readline()
        if not raw:
            return None
        try:
            line = json.loads(raw.decode("utf-8", "replace").strip())
        except json.JSONDecodeError:
            return None
        return self.manager.ingest_reading(line)

    def __iter__(self):
        while True:
            rec = self.read_single()
            if rec is not None:
                yield rec

    def close(self) -> None:
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass





