# dashboard/app.py
import time, sqlite3, pandas as pd
import streamlit as st
import os

st.set_page_config(page_title="Plant Telemetry", layout="wide")

DB_PATH = os.getenv("DB_PATH", "./plant.db")

@st.cache_data(ttl=2.0)
def load_df(limit=2000):
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT received_ts_utc, lux, rh, temp, moisture_pct "
        "FROM reading ORDER BY id DESC LIMIT ?",
        con, params=(limit,)
    )
    con.close()
    if df.empty: return df
    df["received_ts_utc"] = pd.to_datetime(df["received_ts_utc"])
    return df.sort_values("received_ts_utc")

st.title("Plant Telemetry (Live)")
interval = st.sidebar.slider("Refresh (sec)", 1, 10, 3)
run = st.sidebar.checkbox("Run", value=True)
status = st.sidebar.empty()

placeholder = st.empty()

while run:
    df = load_df()
    with placeholder.container():
        if df.empty:
            st.info("No data yet. Is ingest running & writing to this DB?")
        else:
            last = df.iloc[-1]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Lux", f"{last['lux']:.0f}" if pd.notna(last['lux']) else "—")
            c2.metric("RH %", f"{last['rh']:.1f}" if pd.notna(last['rh']) else "—")
            c3.metric("Temp °C", f"{last['temp']:.1f}" if pd.notna(last['temp']) else "—")
            c4.metric("Moisture %", f"{last['moisture_pct']:.1f}" if pd.notna(last['moisture_pct']) else "—")

            st.line_chart(df.set_index("received_ts_utc")[["moisture_pct"]].rename(columns={"moisture_pct":"Moisture %"}))
            st.line_chart(df.set_index("received_ts_utc")[["lux"]].rename(columns={"lux":"Lux"}))
            st.line_chart(df.set_index("received_ts_utc")[["temp","rh"]]
                          .rename(columns={"temp":"Temp °C","rh":"RH %"}))
    status.write(f"Last update: {pd.Timestamp.utcnow().isoformat()}Z")
    time.sleep(interval)
    # re-read sidebar state each loop
    run = st.session_state.get("_run", True) if "_run" in st.session_state else run

