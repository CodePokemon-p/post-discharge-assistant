"""
Minimal persistence layer: patient care plans + a log of every check-in
message, backed by SQLite. This is deliberately simple (no ORM, raw
sqlite3) -- the goal is a real, working data layer today, not premature
infrastructure. If this grows into a multi-user production system,
swapping SQLite for Postgres later means rewriting only this one file,
since nothing outside it should ever import sqlite3 directly.

WHY THIS EXISTS:
Without it, the graph is a stateless function -- call it once, get an
answer, forget everything. A real post-discharge assistant needs to
know "this is patient #1042, here's their care plan from three days
ago" on every new message, and needs a durable record of every
escalation for the nurse dashboard to read from tomorrow.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from graph.schemas import CarePlan

DB_PATH = Path(__file__).parent / "data" / "patients.db"


def init_db() -> None:
    """Create tables if they don't already exist. Call once at startup."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY,
            care_plan_json TEXT NOT NULL,
            language TEXT NOT NULL DEFAULT 'en',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            content TEXT NOT NULL,
            intent TEXT,
            risk_level TEXT,
            answer TEXT,
            escalated INTEGER NOT NULL DEFAULT 0,
            reasoning TEXT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients (patient_id)
        )
    """)
    conn.commit()
    conn.close()


def save_care_plan(patient_id: str, care_plan: CarePlan, language: str = "en") -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO patients (patient_id, care_plan_json, language) VALUES (?, ?, ?)",
        (patient_id, care_plan.model_dump_json(), language),
    )
    conn.commit()
    conn.close()


def get_care_plan(patient_id: str) -> Optional[CarePlan]:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT care_plan_json FROM patients WHERE patient_id = ?", (patient_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return CarePlan(**json.loads(row[0]))


def log_message(
    patient_id: str,
    content: str,
    intent: Optional[str] = None,
    risk_level: Optional[str] = None,
    answer: Optional[str] = None,
    escalated: bool = False,
    reasoning: Optional[str] = None,
) -> None:
    """
    Records one patient check-in and everything the graph decided about
    it. This is the record a nurse dashboard reads from -- every field
    here should be something a nurse would actually want to see.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO messages
           (patient_id, content, intent, risk_level, answer, escalated, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (patient_id, content, intent, risk_level, answer, int(escalated), reasoning),
    )
    conn.commit()
    conn.close()


def get_escalations() -> list[dict]:
    """All escalated messages, newest first -- this IS tomorrow's nurse dashboard data source."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM messages WHERE escalated = 1 ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]