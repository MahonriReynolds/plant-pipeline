import time
from typing import Optional

from plantpipe.storage.database import PlantDBWrapper
from plantpipe.input.serial_ingestor import ProbeReader
from plantpipe.api.api_server import PlantAPI

DB_PATH = "data/plant.db"
SCHEMA_PATH = "sql/001_init.sql"
PROBE_PORT = "/dev/ttyUSB0"
BAUD = 115200
API_HOST = "127.0.0.1"
API_PORT = 8000
RUN_SECONDS = 3600
START_API = True

HARD_CODED_CALIBRATION = {
    "raw_dry": 500,
    "raw_wet": 150,
    "lux_min": 0.0,
    "lux_max": 300000.0,
    "rh_min": 0.0,
    "rh_max": 100.0,
    "temp_min": -40.0,
    "temp_max": 85.0,
    "notes": "Hardcoded default calibration values for testing."
}

def main():
    db = PlantDBWrapper(DB_PATH, SCHEMA_PATH)

    api: Optional[PlantAPI] = None
    if START_API:
        api = PlantAPI(db=db, host=API_HOST, port=API_PORT)
        api.start()
        print(f"API at http://{API_HOST}:{API_PORT}/frontend")

    reader = ProbeReader(
        port=PROBE_PORT,
        baud=BAUD,
        db_wrapper=db,
        defaults=HARD_CODED_CALIBRATION,
        timeout=2.5,
    )

    try:
        start = time.time()
        for inserted_payload in reader:
            last = db.get_last_readings(1, oldest_first=False)
            if last:
                print("Last reading:", last[0])
            if time.time() - start > RUN_SECONDS:
                break
    finally:
        try:
            reader.close()
        except Exception:
            pass
        if api is not None:
            try:
                api.stop()
            except Exception:
                pass
        db.close()

if __name__ == "__main__":
    main()
