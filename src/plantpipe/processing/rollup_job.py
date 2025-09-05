from __future__ import annotations
import argparse
import pathlib
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from plantpipe.storage.database import get_connection, ensure_schema, ensure_rollups

FIVE_MIN = timedelta(minutes=5)

def floor_to_5m(dt: datetime) -> datetime:
    m = dt.minute - (dt.minute % 5)
    return dt.replace(minute=m, second=0, microsecond=0)

def iso_utc(dt: datetime, with_seconds: bool = True) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds" if with_seconds else "minutes")

def run_once(conn: sqlite3.Connection, backfill_minutes: int = 90) -> dict:
    """
    Aggregate closed 5-minute buckets into readings_5m using UPSERT.
    Recompute backfill_minutes into the past to cover late arrivals.
    """
    now = datetime.now(timezone.utc)
    open_bucket_start = floor_to_5m(now)  # current open bucket start
    cutoff_utc = iso_utc(open_bucket_start)  # exclusive upper bound

    lower_bound_dt = open_bucket_start - timedelta(minutes=backfill_minutes)
    lower_utc = iso_utc(lower_bound_dt)

    # Floor ts_utc to 5-min via string math on ISO8601; avoids tz pitfalls.
    bucket_expr = (
        "substr(ts_utc,1,14) || "
        "printf('%02d', (CAST(substr(ts_utc,15,2) AS INTEGER) - (CAST(substr(ts_utc,15,2) AS INTEGER) % 5))) || "
        "':00+00:00'"
    )

    sql = f"""
    WITH base AS (
      SELECT
        plant_id,
        {bucket_expr} AS bucket_start_utc,
        lux, rh, temp, moisture_pct, moisture_raw, err
      FROM readings
      WHERE ts_utc >= ? AND ts_utc < ?
    ),
    agg AS (
      SELECT
        plant_id,
        bucket_start_utc,
        COUNT(*)                      AS row_count,

        AVG(lux)                      AS lux_avg,
        MIN(lux)                      AS lux_min,
        MAX(lux)                      AS lux_max,

        AVG(rh)                       AS rh_avg,
        MIN(rh)                       AS rh_min,
        MAX(rh)                       AS rh_max,

        AVG(temp)                     AS temp_avg,
        MIN(temp)                     AS temp_min,
        MAX(temp)                     AS temp_max,

        AVG(moisture_pct)             AS moisture_pct_avg,
        MIN(moisture_pct)             AS moisture_pct_min,
        MAX(moisture_pct)             AS moisture_pct_max,

        AVG(moisture_raw)             AS moisture_raw_avg,
        MIN(moisture_raw)             AS moisture_raw_min,
        MAX(moisture_raw)             AS moisture_raw_max,

        SUM(CASE WHEN err IS NOT NULL AND err <> '' THEN 1 ELSE 0 END) AS err_count
      FROM base
      GROUP BY plant_id, bucket_start_utc
    )
    INSERT INTO readings_5m (
      plant_id, bucket_start_utc, row_count,
      lux_avg, lux_min, lux_max,
      rh_avg, rh_min, rh_max,
      temp_avg, temp_min, temp_max,
      moisture_pct_avg, moisture_pct_min, moisture_pct_max,
      moisture_raw_avg, moisture_raw_min, moisture_raw_max,
      err_count, updated_at
    )
    SELECT
      plant_id, bucket_start_utc, row_count,
      lux_avg, lux_min, lux_max,
      rh_avg, rh_min, rh_max,
      temp_avg, temp_min, temp_max,
      moisture_pct_avg, moisture_pct_min, moisture_pct_max,
      moisture_raw_avg, moisture_raw_min, moisture_raw_max,
      err_count, DATETIME('now')
    FROM agg
    ON CONFLICT(plant_id, bucket_start_utc) DO UPDATE SET
      row_count          = excluded.row_count,
      lux_avg            = excluded.lux_avg,
      lux_min            = excluded.lux_min,
      lux_max            = excluded.lux_max,
      rh_avg             = excluded.rh_avg,
      rh_min             = excluded.rh_min,
      rh_max             = excluded.rh_max,
      temp_avg           = excluded.temp_avg,
      temp_min           = excluded.temp_min,
      temp_max           = excluded.temp_max,
      moisture_pct_avg   = excluded.moisture_pct_avg,
      moisture_pct_min   = excluded.moisture_pct_min,
      moisture_pct_max   = excluded.moisture_pct_max,
      moisture_raw_avg   = excluded.moisture_raw_avg,
      moisture_raw_min   = excluded.moisture_raw_min,
      moisture_raw_max   = excluded.moisture_raw_max,
      err_count          = excluded.err_count,
      updated_at         = excluded.updated_at
    ;
    """

    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute(sql, (lower_utc, cutoff_utc))
        upserted = cur.rowcount or 0
        # optional rollup_meta (if present in 002_rollups.sql)
        try:
            cur.execute(
                "UPDATE rollup_meta SET last_run_utc = ?, last_cutoff_utc = ? WHERE id = 1",
                (iso_utc(now), cutoff_utc),
            )
        except sqlite3.OperationalError:
            # rollup_meta table may not exist; ignore in MVP
            pass
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"upserted": upserted, "lower_utc": lower_utc, "cutoff_utc": cutoff_utc}

def run_forever(db_path: pathlib.Path, backfill_minutes: int = 90, tick_sec: int = 60):
    conn = get_connection(db_path)
    ensure_schema(conn)
    ensure_rollups(conn)
    try:
        while True:
            now = datetime.now(timezone.utc)
            sleep_s = tick_sec - (now.second % tick_sec)
            time.sleep(sleep_s)
            try:
                stats = run_once(conn, backfill_minutes=backfill_minutes)
                print(f"[rollup] upserted={stats['upserted']} cutoff={stats['cutoff_utc']}")
            except Exception as e:
                print(f"[rollup][error] {e!r}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

def main():
    ap = argparse.ArgumentParser(description="5-minute rollup worker")
    ap.add_argument("--db", default="data/plant.db", help="SQLite DB path")
    ap.add_argument("--once", action="store_true", help="Run a single rollup cycle and exit")
    ap.add_argument("--backfill-minutes", type=int, default=90, help="Recompute this far back each run")
    args = ap.parse_args()

    db_path = pathlib.Path(args.db).resolve()
    conn = get_connection(db_path)
    ensure_schema(conn)
    ensure_rollups(conn)

    if args.once:
        stats = run_once(conn, backfill_minutes=args.backfill_minutes)
        print(stats)
        conn.close()
    else:
        run_forever(db_path, backfill_minutes=args.backfill_minutes)

if __name__ == "__main__":
    main()
