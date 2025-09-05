


import time
from pathlib import Path

from plantpipe.storage.database import ReadingsDBWrapper
from plantpipe.input.serial_ingestor import ProbeReader


# tmp config
db_path = Path("data/readings.db")
schema_path = Path("sql/001_init.sql")
port = "/dev/ttyUSB0"
baud = 115200


# create DB wrapper
db = ReadingsDBWrapper(str(db_path), str(schema_path))
# create probe reader
reader = ProbeReader(port, baud)


# run for 10s
start = time.time()

for payload in reader:
    print(payload)

    # insert test row
    success = db.insert_single_reading(payload)
    print("Insert success:", success)

    # read last row
    rows = db.get_last_readings(1, oldest_first=True)
    print("Last reading:", rows)

    print()

    if time.time() - start > 10:
        break



# close connections
reader.close()
db.close()














