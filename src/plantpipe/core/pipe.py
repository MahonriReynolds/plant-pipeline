


import time
from plantpipe.storage.database import ReadingsDBWrapper
from plantpipe.input.serial_ingestor import ProbeReader
from plantpipe.api.api_server import ReadingsAPI

db_path = "data/readings.db"
schema_path = "sql/001_init.sql"
probe_port = "/dev/ttyUSB0"
baud = 115200
api_host = "127.0.0.1"
api_port = 8000

db = ReadingsDBWrapper(db_path, schema_path)
reader = ProbeReader(probe_port, baud)

api = ReadingsAPI(db=db, host=api_host, port=api_port)
api.start()

try:
    start = time.time()
    for payload in reader:
        print(payload)
        success = db.insert_single_reading(payload)
        print("Insert success:", success)
        rows = db.get_last_readings(1, oldest_first=True)
        print("Last reading:", rows)
        print()
        if time.time() - start > 60:
            break
finally:
    reader.close()
    db.close()
    api.stop()




