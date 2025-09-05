# plantpipe/api/readings_api.py
from __future__ import annotations
import threading
from typing import Optional, List, Any, Dict

import uvicorn
from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from plantpipe.storage.database import ReadingsDBWrapper


class ReadingsAPI:
    """
    Read-only HTTP API backed by a provided ReadingsDBWrapper.
    Lifecycle mirrors your other wrappers: start()/stop().
    """

    def __init__(
        self,
        db: ReadingsDBWrapper,
        host: str = "127.0.0.1",
        port: int = 8000,
        allow_origins: Optional[List[str]] = None,
    ):
        self.db = db
        self.host = host
        self.port = port

        self._app = FastAPI(title="Plant Readings API", version="1.0.0")
        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins or ["*"],  # tighten later
            allow_credentials=True,
            allow_methods=["GET", "OPTIONS"],
            allow_headers=["*"],
        )

        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

        self._define_routes()

    # ---------------- lifecycle ----------------
    def start(self) -> None:
        if self._server and self._server.started:
            return
        config = uvicorn.Config(self._app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)

        def _run():
            self._server.run()

        self._thread = threading.Thread(target=_run, name="ReadingsAPI", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    # ---------------- routes ----------------
    def _define_routes(self) -> None:
        app = self._app
        dbw = self.db  # local alias

        class Reading(BaseModel):
            id: int
            ts: str
            plant: Optional[int] = None
            lux: Optional[float] = None
            rh: Optional[float] = None
            temp_c: Optional[float] = None
            moisture_raw: Optional[int] = None
            moisture_pct: Optional[float] = None
            seq: Optional[int] = None
            err: Optional[str] = None

        def dep_db() -> ReadingsDBWrapper:
            # If you ever swap wrappers, this keeps DI clean
            return dbw

        @app.get("/health")
        def health(db: ReadingsDBWrapper = Depends(dep_db)):
            return {"ok": db.health_check()}

        @app.get("/latest_ts")
        def latest_ts(db: ReadingsDBWrapper = Depends(dep_db)):
            return {"ts": db.latest_timestamp()}

        @app.get("/count")
        def count(db: ReadingsDBWrapper = Depends(dep_db)):
            return {"count": db.row_count()}

        @app.get("/updated_within")
        def updated_within(seconds: int = Query(..., ge=1, le=86400), db: ReadingsDBWrapper = Depends(dep_db)):
            return {"updated": db.updated_within(seconds)}

        @app.get("/has_updates_since")
        def has_updates_since(ts: str = Query(...), db: ReadingsDBWrapper = Depends(dep_db)):
            ok = db.has_updates_since(ts)
            return {"updated": ok}

        @app.get("/last", response_model=List[Reading])
        def last(
            n: int = Query(1, ge=1, le=5000),
            oldest_first: bool = Query(True),
            db: ReadingsDBWrapper = Depends(dep_db),
        ):
            return db.get_last_readings(n=n, oldest_first=oldest_first)

        # Minimal range endpoint using the same shared connection (kept simple for v1)
        @app.get("/range", response_model=List[Reading])
        def range_(
            plant: Optional[int] = Query(None),
            start: Optional[str] = Query(None, description="YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (UTC)"),
            end: Optional[str] = Query(None, description="YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (UTC)"),
            limit: int = Query(1000, ge=1, le=5000),
            oldest_first: bool = Query(True),
            db: ReadingsDBWrapper = Depends(dep_db),
        ):
            # Keep SQL here to avoid expanding the wrapper API right now
            conn = db.connection()

            def parse_ts(s: Optional[str]) -> Optional[str]:
                if not s:
                    return None
                if len(s) == 10:
                    return s + " 00:00:00"
                if len(s) == 19:
                    return s
                raise HTTPException(status_code=400, detail=f"Invalid date format: {s}")

            start_ts = parse_ts(start)
            end_ts = parse_ts(end)

            clauses: List[str] = []
            params: List[Any] = []
            if plant is not None:
                clauses.append("plant = ?")
                params.append(plant)
            if start_ts:
                clauses.append("ts >= ?")
                params.append(start_ts)
            if end_ts:
                clauses.append("ts <= ?")
                params.append(end_ts)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            order = "ASC" if oldest_first else "DESC"

            sql = f"""
                SELECT id, ts, plant, lux, rh, temp_c, moisture_raw, moisture_pct, seq, err
                FROM readings
                {where}
                ORDER BY ts {order}, id {order}
                LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    @property
    def app(self) -> FastAPI:
        return self._app
