"""FastAPI app: POST /events ingestion."""
import os
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from . import db
from .db import get_session, run_migrations

app = FastAPI(title="Webhook Dispatcher", version="1.0.0")


@app.on_event("startup")
def startup():
    with get_session() as session:
        run_migrations(session)

# Default target URL (e.g. from env for Docker)
DEFAULT_TARGET_URL = os.environ.get(
    "TARGET_URL",
    "http://localhost:8080/webhook",
)


class EventIngestion(BaseModel):
    payload: dict
    target_url: str | None = None

    class Config:
        extra = "forbid"


@app.post("/events", status_code=202)
def post_events(event: EventIngestion):
    """Accept event, store in Postgres as pending, return 202 with event id."""
    target_url = event.target_url or DEFAULT_TARGET_URL
    # Basic URL check
    if not target_url.startswith(("http://", "https://")):
        raise HTTPException(422, detail="target_url must be http or https")
    with get_session() as session:
        try:
            event_id = db.insert_event(session, event.payload, target_url)
        except Exception as e:
            raise HTTPException(500, detail=str(e))
    return {
        "id": str(event_id),
        "status": "accepted",
    }


@app.get("/events/{event_id}")
def get_event(event_id: UUID):
    """Return event status (optional, for debugging)."""
    with get_session() as session:
        r = session.execute(
            text(
                "SELECT id, status, attempt_count, next_retry_at, last_error, created_at FROM webhook_events WHERE id = :id"
            ),
            {"id": event_id},
        )
        row = r.fetchone()
    if not row:
        raise HTTPException(404, detail="Event not found")
    return dict(row._mapping)
