import sqlite3
import pandas as pd

db_path = "data/plant.db"
con = sqlite3.connect(db_path)

# Pick the table you want
table_name = "readings"

# Load first 5 rows into a DataFrame
df = pd.read_sql(f"SELECT * FROM {table_name} LIMIT 5;", con)

# Write to CSV
df.to_csv("data/plants_peek.csv", index=False)

con.close()
