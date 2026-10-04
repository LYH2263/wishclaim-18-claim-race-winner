import os, sqlite3
from pathlib import Path

def db_path() -> Path:
    d = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "wishclaim.db"

def connect():
    # isolation_level=None → autocommit; the claim critical section opens its
    # own BEGIN IMMEDIATE. timeout lets a concurrent claimer wait for the
    # winner's write lock instead of failing with "database is locked".
    c = sqlite3.connect(db_path(), isolation_level=None, timeout=10)
    c.row_factory = sqlite3.Row
    return c
