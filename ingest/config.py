import os
from dataclasses import dataclass

@dataclass
class Config:
    serial_port: str = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
    serial_baud: int = int(os.getenv("SERIAL_BAUD", "115200"))
    db_path:str = os.getenv("DB_PATH", "./plant.db")
    plant_id: int = int(os.getenv("PLANT_ID", "1"))
