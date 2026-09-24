"""
Persistence layer: patient care plans + a log of every check-in message,
backed by SQLite. Extended today with phone_number (the scheduler needs
to know who to actually message) and get_all_patients() (so the
scheduler can loop over every patient with a saved care plan).
"""

import json
import sqlite3
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
            phone_number TEXT,
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


def save_care_plan(
    patient_id: str,
    care_plan: CarePlan,
    language: str = "en",
    phone_number: Optional[str] = None,
) -> None:
    conn = sqlite3.connect(DB_PATH)
    # Preserve an existing phone_number if this call doesn't provide one
    # (e.g. extract_care_plan re-saving a plan shouldn't erase a number
    # set earlier).
    if phone_number is None:
        existing = conn.execute(
            "SELECT phone_number FROM patients WHERE patient_id = ?", (patient_id,)
        ).fetchone()
        phone_number = existing[0] if existing else None

    conn.execute(
        "INSERT OR REPLACE INTO patients (patient_id, care_plan_json, language, phone_number) "
        "VALUES (?, ?, ?, ?)",
        (patient_id, care_plan.model_dump_json(), language, phone_number),
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


def get_all_patients() -> list[dict]:
    """
    Every patient with a saved care plan -- what the scheduler loops
    over to send daily check-ins.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM patients").fetchall()
    conn.close()
    results = []
    for row in rows:
        d = dict(row)
        d["care_plan"] = CarePlan(**json.loads(d.pop("care_plan_json")))
        results.append(d)
    return results


def log_message(
    patient_id: str,
    content: str,
    intent: Optional[str] = None,
    risk_level: Optional[str] = None,
    answer: Optional[str] = None,
    escalated: bool = False,
    reasoning: Optional[str] = None,
) -> None:
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
    """All escalated messages, newest first -- tomorrow's nurse dashboard data source."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM messages WHERE escalated = 1 ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]