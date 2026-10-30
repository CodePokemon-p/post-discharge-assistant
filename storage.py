"""
Persistence layer. Postgres-backed.

All functions keep identical signatures to the SQLite version, so no
caller needs to change. Connection is read from DATABASE_URL env var.
"""

import atexit
import json
import os
import threading
from typing import Optional

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from dotenv import load_dotenv
from graph.schemas import CarePlan

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# Meta retries undelivered webhooks for up to ~7 days, so remember message
# IDs for longer than that, then forget them so the table stays small.
PROCESSED_MESSAGE_RETENTION_DAYS = 14


# A small pool of ready-to-use database connections. Opening a fresh connection
# to a remote database costs about a second EVERY time (and can stall for much
# longer when a free-tier database is waking up). The pool opens them once and
# reuses them, so a query takes milliseconds.
_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError(
                        "DATABASE_URL is not set. Add it to .env, e.g.\n"
                        "DATABASE_URL=postgresql://user:password@host:5432/dbname"
                    )
                pool = ConnectionPool(
                    DATABASE_URL,
                    min_size=1,
                    max_size=5,
                    kwargs={"row_factory": dict_row, "connect_timeout": 10},
                    # Test a connection before handing it out. Free-tier databases
                    # drop idle connections; without this a dead one would cause
                    # an error on the next message.
                    check=ConnectionPool.check_connection,
                    max_lifetime=1800,
                    timeout=15,       # wait at most 15s for a free connection
                    open=False,
                )
                pool.open()           # starts connecting in the background
                atexit.register(pool.close)
                _pool = pool
    return _pool


def _get_conn():
    """
    Borrow a connection from the pool (dict-style rows). Used exactly like
    before: `with _get_conn() as conn:` -- on exit the connection goes back to
    the pool instead of being closed.
    """
    return _get_pool().connection()


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
            # The full discharge text, kept so the answering agent can see the
            # WHOLE document (diet, activity, precautions...), not just the few
            # fields the care plan extracted. Added safely to existing databases.
            cur.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS discharge_text TEXT")
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
            # Idempotency: one row per WhatsApp message ID we have accepted.
            # The PRIMARY KEY is what makes "claim" atomic.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Forget old message IDs (housekeeping, runs at startup).
            cur.execute(
                "DELETE FROM processed_messages "
                "WHERE received_at < NOW() - make_interval(days => %s)",
                (PROCESSED_MESSAGE_RETENTION_DAYS,),
            )
        conn.commit()


def claim_message(message_id: str) -> bool:
    """
    Try to claim a WhatsApp message ID for processing.

    Returns True  -> first time we've seen this ID: go ahead and process it.
    Returns False -> already claimed (Meta retry or duplicate): skip it.

    It is a single INSERT ... ON CONFLICT DO NOTHING, so even if two copies
    of the same message arrive at the same instant, exactly one wins.
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO processed_messages (message_id) VALUES (%s) "
                "ON CONFLICT (message_id) DO NOTHING",
                (message_id,),
            )
            claimed = cur.rowcount == 1
        conn.commit()
    return claimed


def save_care_plan(
    patient_id: str,
    care_plan: CarePlan,
    language: str = "en",
    phone_number: Optional[str] = None,
    discharge_text: Optional[str] = None,
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
                INSERT INTO patients
                    (patient_id, care_plan_json, language, phone_number, discharge_text)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (patient_id) DO UPDATE SET
                    care_plan_json = EXCLUDED.care_plan_json,
                    language = EXCLUDED.language,
                    phone_number = EXCLUDED.phone_number,
                    -- callers that don't pass the text must not erase a stored one
                    discharge_text = COALESCE(EXCLUDED.discharge_text, patients.discharge_text)
                """,
                (patient_id, care_plan.model_dump_json(), language, phone_number, discharge_text),
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


def get_discharge_text(patient_id: str) -> Optional[str]:
    """The full original discharge text, or None for patients registered before this existed."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT discharge_text FROM patients WHERE patient_id = %s",
                (patient_id,),
            )
            row = cur.fetchone()
    return row["discharge_text"] if row else None


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