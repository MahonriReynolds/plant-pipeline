from __future__ import annotations
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Literal, Optional
import threading
import uvicorn
from datetime import datetime, timedelta
from pathlib import Path
import re
from plantpipe.storage.database import PlantDBWrapper

Metric = Literal["moisture_pct", "lux", "rh", "temp_c"]
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

class PlantAPI:
    def __init__(self, db: PlantDBWrapper, host: str = "127.0.0.1", port: int = 8000):
        self.db = db
        self.host = host
        self.port = port
        self.app = self._build_app()
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

    # ---------- public controls ----------
    def start(self):
        if self._server is not None:
            return
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)

        def _run():
            # uvicorn Server.run() is blocking; run in a thread
            import asyncio
            asyncio.set_event_loop(asyncio.new_event_loop())
            self._server.run()

        self._thread = threading.Thread(target=_run, name="uvicorn", daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    # ---------- app ----------
    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Plant API", version="0.1.0")

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["*"],
        )

        # --- Serve static frontend at /frontend ---
        # Assuming repo layout:
        # project/
        #   frontend/           <-- your static UI files live here
        #   src/plantpipe/api/api_server.py
        frontend_path = Path(__file__).resolve().parents[3] / "frontend"
        if not frontend_path.exists():
            # If your structure differs, adjust this path calculation.
            raise RuntimeError(f"Frontend folder not found at {frontend_path}")
        app.mount("/frontend", StaticFiles(directory=str(frontend_path), html=True), name="frontend")

        # --- API endpoints ---
        @app.get("/api/health")
        def health():
            return {
                "ok": self.db.health_check(),
                "rows": self.db.row_count(),
                "latest_ts": self.db.latest_timestamp(),
            }

        @app.get("/api/probes")
        def list_probes():
            if not self._table_exists("probes"):
                return []
            cur = self.db.connection().execute(
                "SELECT id, label FROM probes WHERE is_active = 1 ORDER BY id"
            )
            rows = cur.fetchall()
            return [{"id": r["id"], "label": r["label"]} for r in rows]

        @app.get("/api/series")
        def series(
            probe_id: int = Query(..., ge=1),
            metric: Metric = Query(...),
            since_hours: int = Query(24, ge=1, le=24*14),
            limit: int = Query(5000, ge=1, le=20000),
            after_ts: Optional[str] = Query(None, description="return rows with ts > after_ts"),
        ):
            if metric not in {"moisture_pct", "lux", "rh", "temp_c"}:
                raise HTTPException(400, f"Unsupported metric: {metric}")

            if not self._table_exists("readings"):
                return {"series": []}

            if after_ts is not None:
                if not TS_RE.match(after_ts):
                    raise HTTPException(400, "after_ts must be 'YYYY-MM-DD HH:MM:SS'")
                cond_ts = after_ts
                cmp_op = ">"
            else:
                cond_ts = (datetime.utcnow() - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")
                cmp_op = ">="

            sql = f"""
                SELECT ts, {metric} as v
                FROM readings
                WHERE probe_id = ? AND ts {cmp_op} ? AND {metric} IS NOT NULL
                ORDER BY ts ASC, id ASC
                LIMIT ?
            """
            cur = self.db.connection().execute(sql, (probe_id, cond_ts, limit))
            data = [{"ts": r["ts"], "value": r["v"]} for r in cur.fetchall()]
            return {"probe_id": probe_id, "metric": metric, "series": data}

        return app

    def _table_exists(self, name: str) -> bool:
        return self.db.table_exists(name)
