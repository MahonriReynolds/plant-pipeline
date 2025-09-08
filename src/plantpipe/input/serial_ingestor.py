
import json
from datetime import datetime, timezone
import serial
from typing import Optional
from plantpipe.storage.database import PlantDBWrapper





class ProbeManager:
    def __init__(self, db_wrapper: PlantDBWrapper):
        self.db = db_wrapper

    def ensure_probe_has_calibration(self, probe_id: int) -> bool:
        """
        Ensure the probe has an active calibration. 
        Returns True if calibration is found, False if missing and inserted with default values.
        """
        # Check if the probe already has an active calibration
        active_calibration = self.db.get_probe_calibration(probe_id)

        if not active_calibration:
            print(f"Probe {probe_id} does not have an active calibration. Inserting hardcoded default values.")
            
            # If no calibration found, return False to indicate that we need to add a default one
            return False
        
        return True  # Calibration exists, return True

    def calculate_moisture_pct(self, probe_id: int, moisture_raw: int) -> Optional[float]:
        """
        Calculate the moisture percentage using the calibration data for the probe.
        """
        # Get the active calibration data
        calibration_query = """
        SELECT raw_dry, raw_wet FROM probe_calibrations
        WHERE probe_id = ? AND active = 1
        """
        calibration = self.db.conn.execute(calibration_query, (probe_id,)).fetchone()

        if calibration:
            raw_dry, raw_wet = calibration

            # Ensure raw_dry and raw_wet are not equal or invalid
            if raw_dry == raw_wet or raw_dry < 0 or raw_wet < 0:
                raise ValueError("Invalid calibration data (raw_dry and raw_wet cannot be equal or negative).")

            # Calculate moisture_pct based on the calibration
            if raw_wet < raw_dry:
                # Wet values are lower (inverse logic)
                moisture_pct = (1.0 - (moisture_raw - raw_wet) / (raw_dry - raw_wet)) * 100.0
            else:
                # Normal logic
                moisture_pct = ((moisture_raw - raw_wet) / (raw_dry - raw_wet)) * 100.0

            # Ensure the value is within 0-100% range
            moisture_pct = max(0.0, min(moisture_pct, 100.0))
            return moisture_pct
        else:
            print(f"No active calibration found for probe {probe_id}")
            return None

    def validate_sensor_ranges(self, probe_id: int, lux: float, rh: float, temp_c: float, moisture_raw: int) -> bool:
        """
        Validate if all sensor readings are within the expected ranges for the probe.
        """
        # Get the calibration data (lux, rh, temp ranges)
        calibration = self.db.get_probe_calibration(probe_id)

        if calibration:
            lux_min, lux_max = calibration["lux_min"], calibration["lux_max"]
            rh_min, rh_max = calibration["rh_min"], calibration["rh_max"]
            temp_min, temp_max = calibration["temp_min"], calibration["temp_max"]

            # Lux range check
            if lux < lux_min or lux > lux_max:
                print(f"Lux reading {lux} is out of range for probe {probe_id}. Expected between {lux_min} and {lux_max}.")
                return False

            # RH range check
            if rh < rh_min or rh > rh_max:
                print(f"RH reading {rh} is out of range for probe {probe_id}. Expected between {rh_min} and {rh_max}.")
                return False

            # Temperature range check
            if temp_c < temp_min or temp_c > temp_max:
                print(f"Temperature reading {temp_c} is out of range for probe {probe_id}. Expected between {temp_min} and {temp_max}.")
                return False

            # Moisture raw range check
            if moisture_raw < min(lux_min, lux_max) or moisture_raw > max(lux_min, lux_max):
                print(f"Moisture reading {moisture_raw} is out of range for probe {probe_id}.")
                return False

            return True
        else:
            print(f"No active calibration found for probe {probe_id}. Cannot validate sensor readings.")
            return False




class ProbeReader:
    def __init__(self, port: str, baud: int, db_wrapper: PlantDBWrapper, timeout: int = 2.5) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.db = db_wrapper
        self.probe_manager = ProbeManager(db_wrapper)  # Initialize ProbeManager

        self.ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
    
    def read_single(self):
        raw = self.ser.readline()
        if not raw:
            return None

        try:
            line = json.loads(raw.decode("utf-8", "replace").strip())
        except json.JSONDecodeError:
            return None

        probe_id = line.get("plant_id")
        
        # Ensure the probe has a valid calibration
        self.probe_manager.ensure_probe_has_calibration(probe_id)

        # Extract sensor readings
        lux = line.get("lux")
        rh = line.get("rh")
        temp_c = line.get("temp")
        moisture_raw = line.get("moisture_raw")
        
        # Validate if all readings are within valid ranges
        if not self.probe_manager.validate_sensor_ranges(probe_id, lux, rh, temp_c, moisture_raw):
            # If any reading is invalid, skip this reading
            print(f"Skipping invalid reading for probe {probe_id}")
            return None

        # Calculate moisture percentage based on the moisture_raw and calibration
        moisture_pct = self.probe_manager.calculate_moisture_pct(probe_id, moisture_raw)

        # Return the data including the calculated moisture_pct
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "ts": ts,
            "probe_id": str(probe_id),
            "lux": lux,
            "rh": rh,
            "temp_c": temp_c,
            "moisture_raw": moisture_raw,
            "moisture_pct": moisture_pct,  # Return the calculated moisture_pct
            "seq": line.get("seq"),
            "err": line.get("err") or "",
        }

    def stream(self):
        while True:
            rec = self.read_single()
            if rec is not None:
                yield rec
    
    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        
    def __iter__(self):
        return self.stream()
