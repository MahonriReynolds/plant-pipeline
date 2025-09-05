


from datetime import datetime, timezone
from pathlib import Path

from plantpipe.storage.database import ReadingsDBWrapper

# Paths
db_path = Path("data/readings.db")
schema_path = Path("sql/001_init.sql")

# Make sure the data folder exists
db_path.parent.mkdir(parents=True, exist_ok=True)

# Create DB wrapper
db = ReadingsDBWrapper(str(db_path), str(schema_path))


# Test payload
payload = {
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    "lux": 123.4,
    "rh": 41.2,
    "temp_c": 22.8,
    "moisture_raw": 480,
    "moisture_pct": 37.5,
    "seq": 1,
    "err": None,
}


input(payload["ts"])

# Insert test row
success = db.insert_single_reading(payload)
print("Insert success:", success)

# Read last row(s)
rows = db.get_last_readings(1, oldest_first=True)
print("Last reading:", rows)

# Close connection
db.close()






