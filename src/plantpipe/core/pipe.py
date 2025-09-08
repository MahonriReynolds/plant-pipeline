


import time
from plantpipe.storage.database import PlantDBWrapper
from plantpipe.input.serial_ingestor import ProbeReader, ProbeManager
from plantpipe.api.api_server import PlantAPI

db_path = "data/plant.db"
schema_path = "sql/001_init.sql"
probe_port = "/dev/ttyUSB0"
baud = 115200
api_host = "127.0.0.1"
api_port = 8000

# Initialize DB Wrapper and Probe Manager
db = PlantDBWrapper(db_path, schema_path)
probe_manager = ProbeManager(db)  # Initialize ProbeManager for validation & calculation

# Initialize Probe Reader to read data from the probe
reader = ProbeReader(probe_port, baud, db)

# Hardcoded Default Calibration Values
HARD_CODED_CALIBRATION = {
    "raw_dry": 450,       # Example value for dry calibration
    "raw_wet": 190,       # Example value for wet calibration
    "lux_min": 0.0,       # Example minimum lux value
    "lux_max": 300000.0,  # Example maximum lux value
    "rh_min": 0.0,        # Example minimum humidity
    "rh_max": 100.0,      # Example maximum humidity
    "temp_min": -40.0,    # Example minimum temperature
    "temp_max": 85.0,     # Example maximum temperature
    "notes": "Hardcoded default calibration values for testing."
}

# Function to insert hardcoded calibration values
def insert_hardcoded_calibration(probe_id: int):
    print(f"Probe {probe_id} does not have a calibration. Inserting hardcoded calibration values.")

    # Insert hardcoded calibration into the database
    db.set_active_calibration(
        probe_id,
        HARD_CODED_CALIBRATION['raw_dry'],
        HARD_CODED_CALIBRATION['raw_wet'],
        HARD_CODED_CALIBRATION['lux_min'],
        HARD_CODED_CALIBRATION['lux_max'],
        HARD_CODED_CALIBRATION['rh_min'],
        HARD_CODED_CALIBRATION['rh_max'],
        HARD_CODED_CALIBRATION['temp_min'],
        HARD_CODED_CALIBRATION['temp_max'],
        HARD_CODED_CALIBRATION['notes']
    )

# api = PlantAPI(db=db, host=api_host, port=api_port)
# api.start()

try:
    start = time.time()
    for payload in reader:
        probe_id = payload.get("probe_id")
        
        # Ensure the probe has a valid calibration (using hardcoded values if needed)
        has_calibration = probe_manager.ensure_probe_has_calibration(probe_id)

        if not has_calibration:
            insert_hardcoded_calibration(probe_id)

        # Validate the sensor readings against the calibrated ranges
        lux = payload.get("lux")
        rh = payload.get("rh")
        temp_c = payload.get("temp_c")
        moisture_raw = payload.get("moisture_raw")

        if not probe_manager.validate_sensor_ranges(probe_id, lux, rh, temp_c, moisture_raw):
            print(f"Skipping invalid reading for probe {probe_id}")
            continue  # Skip this reading if invalid

        # Calculate moisture percentage
        moisture_pct = probe_manager.calculate_moisture_pct(probe_id, moisture_raw)
        if moisture_pct is None:
            print(f"Skipping reading due to missing calibration for probe {probe_id}")
            continue  # Skip if moisture calculation is not possible

        # Update payload with calculated moisture percentage
        payload['moisture_pct'] = moisture_pct

        # Insert the valid reading into the database
        success = db.insert_single_reading(payload)
        print("Insert success:", success)

        # Optionally, fetch and print the last reading to confirm insertion
        rows = db.get_last_readings(1, oldest_first=True)
        print("Last reading:", rows)
        print()

        # Limit the script to 60 seconds of reading for this run
        if time.time() - start > 60:
            break
finally:
    reader.close()
    db.close()
    # api.stop()
