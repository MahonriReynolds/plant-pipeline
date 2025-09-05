

import sqlite3
import pathlib

BASE_DIR = pathlib.Path(__file__).resolve().parents[3]  # repo root
DB_PATH = BASE_DIR / "data" / "plant.db"
SCHEMA_PATH = BASE_DIR / "sql" / "001_init.sql"


def get_connection(db_path: pathlib.Path = DB_PATH) -> sqlite3.Connection:
    """Open SQLite connection with WAL + pragmas set."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection, schema_path: pathlib.Path = SCHEMA_PATH):
    """Run schema init script if tables are missing."""
    # Simple presence check
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='readings';"
    )
    if cur.fetchone() is None:
        sql_text = schema_path.read_text()
        conn.executescript(sql_text)
        conn.commit()


def init_db():
    """One-shot initializer: open conn and ensure schema exists."""
    conn = get_connection()
    ensure_schema(conn)
    return conn


