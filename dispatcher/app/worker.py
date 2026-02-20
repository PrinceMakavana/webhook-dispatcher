"""Background worker: poll Postgres, claim pending events, deliver with HMAC, backoff on failure."""
import json
import logging
import os
import random
import time
from datetime import datetime, timezone, timedelta
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from .db import SessionLocal, claim_pending_events, record_attempt, mark_delivered, mark_failed
from .sign import sign_payload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dispatcher.worker")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me-in-production")
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "15"))
POLL_INTERVAL = float(os.environ.get("WORKER_POLL_INTERVAL", "1.5"))
CLAIM_LIMIT = int(os.environ.get("WORKER_CLAIM_LIMIT", "10"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "20"))
BACKOFF_BASE_SECONDS = float(os.environ.get("BACKOFF_BASE_SECONDS", "2"))
BACKOFF_MAX_SECONDS = float(os.environ.get("BACKOFF_MAX_SECONDS", "3600"))  # 1 hour


def backoff_with_jitter(attempt_count: int) -> datetime:
    """Exponential backoff with jitter. next_retry_at = now + base * 2^attempt + jitter."""
    delay = min(
        BACKOFF_BASE_SECONDS * (2 ** attempt_count) + random.uniform(0, 1),
        BACKOFF_MAX_SECONDS,
    )
    return datetime.now(timezone.utc) + timedelta(seconds=delay)


def deliver_one(session: Session, event: dict) -> None:
    """Send one event to target_url with HMAC; update DB on success/failure."""
    event_id = event["id"]
    target_url = event["target_url"]
    payload = event["payload"]
    attempt_count = event["attempt_count"]
    attempt_number = attempt_count + 1

    body = json.dumps(payload).encode("utf-8")
    signature = sign_payload(WEBHOOK_SECRET, body)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
    }

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(target_url, content=body, headers=headers)
        status_code = resp.status_code
        response_body = resp.text[:2000] if resp.text else None
        error = None
    except Exception as e:
        status_code = None
        response_body = None
        error = str(e)
        logger.warning("Delivery failed event_id=%s attempt=%s error=%s", event_id, attempt_number, error)

    record_attempt(
        session,
        UUID(str(event_id)),
        attempt_number,
        status_code,
        response_body,
        error,
    )

    if status_code is not None and 200 <= status_code < 300:
        mark_delivered(session, UUID(str(event_id)))
        logger.info("Delivered event_id=%s after %s attempt(s)", event_id, attempt_number)
        return

    # Failure: backoff or mark dead
    next_attempt_count = attempt_count + 1
    next_retry_at = backoff_with_jitter(next_attempt_count)
    last_error = error or f"HTTP {status_code}: {response_body or 'no body'}"
    mark_dead = next_attempt_count >= MAX_ATTEMPTS
    mark_failed(
        session,
        UUID(str(event_id)),
        next_attempt_count,
        next_retry_at,
        last_error,
        mark_dead=mark_dead,
    )
    if mark_dead:
        logger.error("Event dead after %s attempts event_id=%s", MAX_ATTEMPTS, event_id)
    else:
        logger.info(
            "Will retry event_id=%s at %s (attempt %s)",
            event_id,
            next_retry_at.isoformat(),
            next_attempt_count,
        )


def run_worker_loop() -> None:
    """Poll DB, claim events, deliver; run until KeyboardInterrupt."""
    logger.info("Worker started (poll_interval=%s, claim_limit=%s)", POLL_INTERVAL, CLAIM_LIMIT)
    while True:
        try:
            session = SessionLocal()
            try:
                events = claim_pending_events(session, limit=CLAIM_LIMIT)
                for event in events:
                    try:
                        deliver_one(session, event)
                        session.commit()
                    except Exception as e:
                        logger.exception("Error delivering event_id=%s: %s", event.get("id"), e)
                        session.rollback()
            finally:
                session.close()
        except Exception as e:
            logger.exception("Worker loop error: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_worker_loop()
