from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ---------- Defaults ----------
DEFAULT_DB = "data/plant.db"
DEFAULT_SPOOL = "data/ingest_spool.ndjson"
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"  # or COM3 on Windows
DEFAULT_BAUD = 115200
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8080

def project_root() -> Path:
    # this file is src/plantpipe/core/pipe.py
    return Path(__file__).resolve().parents[3]

@dataclass
class ChildSpec:
    name: str
    argv: List[str]
    cwd: Path
    env: Optional[dict] = None
    restart: bool = True
    process: Optional[subprocess.Popen] = field(default=None, init=False)
    backoff_sec: float = field(default=1.0, init=False)

    def spawn(self):
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            self.argv,
            cwd=str(self.cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            env=self.env,
        )
        print(f"[orch] started {self.name} pid={self.process.pid} :: {' '.join(self.argv)}", flush=True)

    def terminate(self, sig=signal.SIGTERM):
        if self.process and self.process.poll() is None:
            try:
                self.process.send_signal(sig)
            except Exception:
                pass

    def kill(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.kill()
            except Exception:
                pass

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

class Orchestrator:
    def __init__(self, children: List[ChildSpec]):
        self.children = children
        self._stop = False

    def start_all(self):
        for ch in self.children:
            ch.spawn()

    def _drain_logs(self, ch: ChildSpec):
        if not ch.process or not ch.process.stdout:
            return
        drained = 0
        while drained < 200:
            line = ch.process.stdout.readline()
            if not line:
                break
            print(f"[{ch.name}] {line.rstrip()}", flush=True)
            drained += 1

    def _maybe_restart(self, ch: ChildSpec):
        if not ch.restart:
            return
        if ch.process and ch.process.poll() is not None:
            code = ch.process.returncode
            print(f"[orch] {ch.name} exited code={code}", flush=True)
            sleep_s = min(ch.backoff_sec, 30.0)
            print(f"[orch] restarting {ch.name} in {sleep_s:.1f}s", flush=True)
            time.sleep(sleep_s)
            ch.backoff_sec = min(ch.backoff_sec * 2.0, 30.0)
            ch.spawn()

    def run(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._on_signal)

        self.start_all()
        try:
            while not self._stop:
                any_running = False
                for ch in self.children:
                    self._drain_logs(ch)
                    if ch.is_running():
                        any_running = True
                    else:
                        self._maybe_restart(ch)
                if not any_running and not self._stop:
                    time.sleep(0.25)
                time.sleep(0.05)
        finally:
            self._shutdown()

    def _on_signal(self, signum, frame):
        print(f"[orch] signal={signum} received; shutting down…", flush=True)
        self._stop = True

    def _shutdown(self):
        for ch in self.children:
            ch.terminate(signal.SIGINT)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            all_done = True
            for ch in self.children:
                if ch.is_running():
                    all_done = False
                    self._drain_logs(ch)
            if all_done:
                break
            time.sleep(0.1)
        for ch in self.children:
            if ch.is_running():
                print(f"[orch] killing {ch.name}", flush=True)
                ch.kill()
        for ch in self.children:
            self._drain_logs(ch)
        print("[orch] shutdown complete", flush=True)

def parse_args():
    ap = argparse.ArgumentParser(description="Plant pipeline orchestrator")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite DB path")
    ap.add_argument("--spool", default=DEFAULT_SPOOL, help="NDJSON spool path")
    ap.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port (e.g., /dev/ttyUSB0 or COM3)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baud rate")
    ap.add_argument("--timeout", type=float, default=2.5, help="Serial read timeout")
    ap.add_argument("--flush-every", type=int, default=25, help="Commit every N rows")
    ap.add_argument("--max-retries", type=int, default=0, help="Serial open retries (0=no retries)")
    ap.add_argument("--no-api", action="store_true", help="Do not start API server")
    ap.add_argument("--api-host", default=DEFAULT_API_HOST, help="API bind host")
    ap.add_argument("--api-port", type=int, default=DEFAULT_API_PORT, help="API bind port")
    ap.add_argument("--rollup-backfill-minutes", type=int, default=90, help="Backfill window for 5m rollups")
    ap.add_argument("--python", default=sys.executable, help="Python interpreter for child processes")
    return ap.parse_args()

def resolve_and_prepare_paths(args):
    db_abs = Path(args.db).expanduser().resolve()
    spool_abs = Path(args.spool).expanduser().resolve()
    db_abs.parent.mkdir(parents=True, exist_ok=True)
    spool_abs.parent.mkdir(parents=True, exist_ok=True)
    return str(db_abs), str(spool_abs)

def build_children(args) -> List[ChildSpec]:
    root = project_root()
    src_dir = root / "src"
    py = args.python

    ingestor_argv = [
        py, "-m", "plantpipe.input.serial_ingestor",
        "--port", args.port,
        "--baud", str(args.baud),
        "--db", args.db,                 # absolute
        "--spool", args.spool,           # absolute
        "--flush-every", str(args.flush_every),
        "--timeout", str(args.timeout),
        "--max-retries", str(args.max_retries),
        "--print",
    ]
    ingestor = ChildSpec(name="ingestor", argv=ingestor_argv, cwd=src_dir, restart=True)

    rollup_argv = [
        py, "-m", "plantpipe.processing.rollup_job",
        "--db", args.db,                 # absolute
        "--backfill-minutes", str(args.rollup_backfill_minutes),
    ]
    rollups = ChildSpec(name="rollups-5m", argv=rollup_argv, cwd=src_dir, restart=True)

    children = [ingestor, rollups]

    if not args.no_api:
        api_argv = [
            py, "-m", "plantpipe.api.api_server",
            "--db", args.db,
            "--host", args.api_host,
            "--port", str(args.api_port),
        ]
        children.append(ChildSpec(name="api", argv=api_argv, cwd=src_dir, restart=True))

    return children

def main():
    args = parse_args()
    db_abs, spool_abs = resolve_and_prepare_paths(args)
    args.db = db_abs
    args.spool = spool_abs

    children = build_children(args)
    Orchestrator(children).run()

if __name__ == "__main__":
    main()
