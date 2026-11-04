"""Signed, expiring tokens for the confirmation and resubmit links (HMAC-SHA256, key from SSM).

The form Lambda carries a copy of this logic; keep the two in step (the format is `payload.signature`, both base64url).
"""
import base64
import hashlib
import hmac
import json
import time


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key, claims, ttl_seconds, now=None):
    body = _b64(json.dumps({**claims, "exp": int((now if now is not None else time.time()) + ttl_seconds)}, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(key.encode(), body.encode(), hashlib.sha256).digest())}"


def verify(key, token, now=None):
    """The claims if the signature is valid and the token has not expired, else None. Never raises on bad input."""
    try:
        body, sig = token.split(".")
        good = hmac.new(key.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(good, _unb64(sig)):
            return None
        claims = json.loads(_unb64(body))
        return claims if claims["exp"] > (now if now is not None else time.time()) else None
    except Exception:
        return None
