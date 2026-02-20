"""HMAC-SHA256 signing for webhook payloads. Receiver verifies with shared secret."""
import hmac
import hashlib


def sign_payload(secret: str, body: bytes) -> str:
    """Compute HMAC-SHA256(secret, body) and return hex string for X-Webhook-Signature."""
    sig = hmac.new(
        secret.encode("utf-8") if isinstance(secret, str) else secret,
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={sig}"
