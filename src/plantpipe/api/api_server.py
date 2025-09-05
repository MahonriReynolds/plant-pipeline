# src/plantpipe/api/api_server.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from plantpipe.storage.database import get_connection
from typing import List, Optional
import pathlib

# Initialize the FastAPI app
app = FastAPI()

# Helper to get the DB connection
def get_db():
    return get_connection(str(pathlib.Path("data/plant.db").resolve()))

# Models for Pydantic validation (you can expand these if needed)
class Reading(BaseModel):
    ts_utc: str
    lux: Optional[float]
    rh: Optional[float]
    temp: Optional[float]
    moisture_pct: Optional[float]
    moisture_raw: int
    err: str

class Rollup(BaseModel):
    bucket_start_utc: str
    lux_avg: Optional[float]
    rh_avg: Optional[float]
    temp_avg: Optional[float]
    moisture_pct_avg: Optional[float]
    moisture_raw_avg: Optional[float]

@app.get("/plant/{plant_id}/latest", response_model=Reading)
def get_latest_reading(plant_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ts_utc, lux, rh, temp, moisture_pct, moisture_raw, err
        FROM readings
        WHERE plant_id = ?
        ORDER BY ts_utc DESC LIMIT 1
    """, (plant_id,))
    row = cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reading not found")
    return {
        "ts_utc": row[0],
        "lux": row[1],
        "rh": row[2],
        "temp": row[3],
        "moisture_pct": row[4],
        "moisture_raw": row[5],
        "err": row[6]
    }

@app.get("/plant/{plant_id}/timeseries", response_model=List[Rollup])
def get_timeseries(plant_id: int, bucket: str = "5m"):
    if bucket != "5m":
        raise HTTPException(status_code=400, detail="Unsupported bucket size")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bucket_start_utc, lux_avg, rh_avg, temp_avg, moisture_pct_avg, moisture_raw_avg
        FROM readings_5m
        WHERE plant_id = ?
        ORDER BY bucket_start_utc DESC
        LIMIT 48
    """, (plant_id,))
    rows = cursor.fetchall()
    return [
        Rollup(
            bucket_start_utc=row[0],
            lux_avg=row[1],
            rh_avg=row[2],
            temp_avg=row[3],
            moisture_pct_avg=row[4],
            moisture_raw_avg=row[5]
        )
        for row in rows
    ]

@app.get("/health")
def get_health():
    return {"status": "ok"}
