"""
Chaotic mock webhook receiver: verifies HMAC, then ~70% failure (500/timeout),
random delays 0–5s, occasional hang. Proves dispatcher backoff and eventual success.
"""
import asyncio
import hmac
import hashlib
import logging
import os
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_receiver")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me-in-production")
FAILURE_RATE = float(os.environ.get("FAILURE_RATE", "0.7"))  # 70% fail
MAX_DELAY_SEC = float(os.environ.get("MAX_DELAY_SEC", "5"))
HANG_RATE = float(os.environ.get("HANG_RATE", "0.08"))  # 8% hold connection (timeout)
PORT = int(os.environ.get("PORT", "8080"))


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time compare of X-Webhook-Signature with HMAC-SHA256(secret, body)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = header.removeprefix("sha256=")
    computed = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, expected)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Mock receiver started (failure_rate=%s, max_delay=%s)", FAILURE_RATE, MAX_DELAY_SEC)
    yield


app = FastAPI(lifespan=lifespan)

@app.get("/webhook")
async def webhook_get():
    """Allow GET for browser/health checks; return hint to use POST for delivery."""
    return Response(
        status_code=200,
        content='{"message": "Webhook receiver. Use POST with X-Webhook-Signature to deliver webhooks."}',
        media_type="application/json",
    )


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Webhook-Signature")

    if not verify_signature(WEBHOOK_SECRET, body, sig):
        logger.warning("Invalid or missing signature")
        return Response(status_code=401, content="Invalid signature")

    # Occasional "offline": hold connection until client times out
    if random.random() < HANG_RATE:
        logger.info("Simulating offline: holding connection")
        await asyncio.sleep(60)
        return Response(status_code=504, content="Gateway Timeout (simulated)")

    # Random delay 0–MAX_DELAY_SEC
    delay = random.uniform(0, MAX_DELAY_SEC)
    logger.info("Delay %.2fs then respond", delay)
    await asyncio.sleep(delay)

    # ~70% failure: 500 or "timeout" (we already might have delayed a lot)
    if random.random() < FAILURE_RATE:
        logger.info("Returning 500 (chaos)")
        return Response(status_code=500, content="Internal Server Error (chaos)")

    logger.info("Success 200 body_len=%s", len(body))
    return Response(status_code=200, content='{"received": true}')
