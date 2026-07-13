# ---------------------------------------------------------------------------
# File:        db.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: SQLite connection helper plus run-tracking utilities
#              (start_run/finish_run) shared across all pipeline scripts.
# ---------------------------------------------------------------------------
import sqlite3
from pathlib import Path

DB_PATH = Path("survey.db")

def connect(db_path=DB_PATH):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    return con

def init_db(db_path=DB_PATH, schema_path="schema.sql"):
    con = connect(db_path)
    with open(schema_path, "r", encoding="utf-8") as f:
        con.executescript(f.read())
    con.commit()
    con.close()

def start_run(con, run_type, notes=""):
    cur = con.execute(
        "INSERT INTO runs(run_type, notes) VALUES (?, ?)",
        (run_type, notes),
    )
    con.commit()
    return int(cur.lastrowid)

def finish_run(con, run_id, status="finished", notes=None):
    if notes is None:
        con.execute(
            "UPDATE runs SET finished_at=CURRENT_TIMESTAMP, status=? WHERE run_id=?",
            (status, run_id),
        )
    else:
        con.execute(
            "UPDATE runs SET finished_at=CURRENT_TIMESTAMP, status=?, notes=? WHERE run_id=?",
            (status, notes, run_id),
        )
    con.commit()
