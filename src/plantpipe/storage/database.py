from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from typing import Union
import time

PathLike = Union[str, Path]

# --- Resolve project root from this file's location ---
def _project_root() -> Path:
    # this file: .../src/plantpipe/storage/database.py
    return Path(__file__).resolve().parents[3]

def _load_sql_text(filename: str) -> str:
    """
    Load an .sql file robustly:
    1) PLANTPIPE_SQL_DIR (if set),
    2) <repo-root>/sql/,
    3) <cwd>/sql/ (last resort).
    """
    candidates: list[Path] = []
    env_dir = os.environ.get("PLANTPIPE_SQL_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(_project_root() / "sql")
    candidates.append(Path.cwd() / "sql")
    for base in candidates:
        p = base / filename
        if p.exists():
            return p.read_text(encoding="utf-8")
    tried = [str((b / filename)) for b in candidates]
    raise FileNotFoundError(f"Could not find SQL file '{filename}'. Tried: {tried}")

def get_connection(db_path: PathLike) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Wait up to 5s if another process is holding a write lock
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    # Ask SQLite itself to wait on busy locks too
    conn.execute("PRAGMA busy_timeout=5000;")
    # Set WAL; if another process is racing, retry once after a short sleep
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError:
        time.sleep(0.2)
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    # Your 001_init.sql should contain STRICT tables, indices, CHECKs, etc.
    sql_text = _load_sql_text("001_init.sql")
    conn.executescript(sql_text)
    conn.commit()

def ensure_rollups(conn: sqlite3.Connection) -> None:
    sql_text = _load_sql_text("002_rollups.sql")
    conn.executescript(sql_text)
    conn.commit()
