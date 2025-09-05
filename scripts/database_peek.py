#!/usr/bin/env python3
"""
Quick peek into the SQLite DB → CSV
"""

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/plant.db")
OUT_CSV = Path("data/plants_peek.csv")

def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}")

    with sqlite3.connect(DB_PATH) as con:
        # List tables
        tables = pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;", con
        )
        print("Tables in DB:", tables["name"].tolist())

        table_name = "readings"
        if table_name not in tables["name"].values:
            raise ValueError(f"Table '{table_name}' not found in database.")

        # Fetch a sample
        df = pd.read_sql(f"SELECT * FROM {table_name} LIMIT 5;", con)

    # Save to CSV
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"Wrote preview to {OUT_CSV} ({len(df)} rows)")

if __name__ == "__main__":
    main()
