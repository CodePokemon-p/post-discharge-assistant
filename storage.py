"""
Persistence layer. Postgres-backed.

All functions keep identical signatures to the SQLite version, so no
caller needs to change. Connection is read from DATABASE_URL env var.
"""

import json
import os
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from graph.schemas import CarePlan

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


def _get_conn():
    """Open a new Postgres connection with dict-style row access."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env, e.g.\n"
            "DATABASE_URL=postgresql://user:password@host:5432/dbname"
        )
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    """Create tables if they don't exist. Idempotent."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS patients (
                    patient_id TEXT PRIMARY KEY,
                    care_plan_json TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'en',
                    phone_number TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    intent TEXT,
                    risk_level TEXT,
                    answer TEXT,
                    escalated INTEGER NOT NULL DEFAULT 0,
                    priority TEXT,
                    source TEXT,
                    reasoning TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (patient_id) REFERENCES patients (patient_id)
                )
            """)
        conn.commit()


def save_care_plan(
    patient_id: str,
    care_plan: CarePlan,
    language: str = "en",
    phone_number: Optional[str] = None,
) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            if phone_number is None:
                cur.execute(
                    "SELECT phone_number FROM patients WHERE patient_id = %s",
                    (patient_id,),
                )
                existing = cur.fetchone()
                phone_number = existing["phone_number"] if existing else None

            cur.execute(
                """
                INSERT INTO patients (patient_id, care_plan_json, language, phone_number)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (patient_id) DO UPDATE SET
                    care_plan_json = EXCLUDED.care_plan_json,
                    language = EXCLUDED.language,
                    phone_number = EXCLUDED.phone_number
                """,
                (patient_id, care_plan.model_dump_json(), language, phone_number),
            )
        conn.commit()


def get_care_plan(patient_id: str) -> Optional[CarePlan]:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT care_plan_json FROM patients WHERE patient_id = %s",
                (patient_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return CarePlan(**json.loads(row["care_plan_json"]))


def get_all_patients() -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM patients")
            rows = cur.fetchall()
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
    priority: Optional[str] = None,
    source: Optional[str] = None,
    reasoning: Optional[str] = None,
) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO messages
                   (patient_id, content, intent, risk_level, answer,
                    escalated, priority, source, reasoning)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    patient_id, content, intent, risk_level, answer,
                    int(escalated), priority, source, reasoning,
                ),
            )
        conn.commit()


def get_escalations() -> list[dict]:
    """All escalated messages, urgent first, then newest within each tier."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM messages WHERE escalated = 1
                   ORDER BY CASE priority WHEN 'urgent' THEN 0 ELSE 1 END,
                            timestamp DESC"""
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_recent_messages(patient_id: str, limit: int = 20, days: int = 7) -> list[dict]:
    """
    Last N messages for a patient within the last D days, oldest first
    (so prompts read chronologically).
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content, answer, intent, timestamp
                FROM messages
                WHERE patient_id = %s
                  AND timestamp >= NOW() - make_interval(days => %s)
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (patient_id, days, limit),
            )
            rows = cur.fetchall()
    return [dict(r) for r in reversed(rows)]


def get_all_messages(limit: int = 200) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM messages ORDER BY timestamp DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]