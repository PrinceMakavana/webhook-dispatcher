"""Postgres connection and queries for webhook_events and delivery_attempts."""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/webhook_dispatcher",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_migrations(session: Session) -> None:
    """Run SQL migrations from dispatcher/migrations/."""
    migrations_dir = os.path.join(os.path.dirname(__file__), "..", "migrations")
    for name in sorted(os.listdir(migrations_dir)):
        if not name.endswith(".sql"):
            continue
        path = os.path.join(migrations_dir, name)
        with open(path) as f:
            session.execute(text(f.read()))


def insert_event(session: Session, payload: dict, target_url: str) -> UUID:
    """Insert a pending event; next_retry_at = now() so worker picks it up."""
    now = datetime.now(timezone.utc)
    payload_json = json.dumps(payload) if isinstance(payload, dict) else payload
    r = session.execute(
        text("""
            INSERT INTO webhook_events (payload, target_url, status, next_retry_at, attempt_count)
            VALUES (CAST(:payload AS jsonb), :target_url, 'pending', :now, 0)
            RETURNING id
        """),
        {"payload": payload_json, "target_url": target_url, "now": now},
    )
    row = r.fetchone()
    return row[0]


def claim_pending_events(session: Session, limit: int = 10):
    """Select pending events ready for retry; claim with FOR UPDATE SKIP LOCKED."""
    r = session.execute(
        text("""
            SELECT id, payload, target_url, attempt_count
            FROM webhook_events
            WHERE status = 'pending'
              AND (next_retry_at IS NULL OR next_retry_at <= now())
            ORDER BY created_at
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
        """),
        {"limit": limit},
    )
    return [dict(row._mapping) for row in r.fetchall()]


def record_attempt(
    session: Session,
    event_id: UUID,
    attempt_number: int,
    status_code: int | None,
    response_body: str | None,
    error: str | None,
) -> None:
    session.execute(
        text("""
            INSERT INTO delivery_attempts (event_id, attempt_number, status_code, response_body, error)
            VALUES (:event_id, :attempt_number, :status_code, :response_body, :error)
        """),
        {
            "event_id": event_id,
            "attempt_number": attempt_number,
            "status_code": status_code,
            "response_body": response_body,
            "error": error,
        },
    )


def mark_delivered(session: Session, event_id: UUID) -> None:
    now = datetime.now(timezone.utc)
    session.execute(
        text("""
            UPDATE webhook_events
            SET status = 'delivered', updated_at = :now, last_error = NULL
            WHERE id = :event_id
        """),
        {"event_id": event_id, "now": now},
    )


def mark_failed(
    session: Session,
    event_id: UUID,
    attempt_count: int,
    next_retry_at: datetime,
    last_error: str,
    mark_dead: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    status = "dead" if mark_dead else "pending"
    session.execute(
        text("""
            UPDATE webhook_events
            SET status = :status, updated_at = :now, attempt_count = :attempt_count,
                next_retry_at = :next_retry_at, last_error = :last_error
            WHERE id = :event_id
        """),
        {
            "event_id": event_id,
            "status": status,
            "now": now,
            "attempt_count": attempt_count,
            "next_retry_at": next_retry_at,
            "last_error": last_error,
        },
    )
