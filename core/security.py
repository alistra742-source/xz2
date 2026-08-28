"""Crypto helpers, rate limiting and request-integrity utilities."""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

from . import config

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def new_secret_key():
    """The long string a user must keep safe. 4x16 base32-ish groups."""
    raw = secrets.token_bytes(48)
    b32 = base64.b32encode(raw).decode().rstrip("=")
    groups = [b32[i:i + 8] for i in range(0, len(b32), 8)]
    return "CF-" + "-".join(groups)


def hash_secret(secret: str) -> str:
    secret = (secret or "").strip()
    return hashlib.sha256(("cfsalt|" + secret).encode()).hexdigest()


def new_session_token():
    return secrets.token_urlsafe(36)


def random_username():
    return "".join(secrets.choice(_ALPHABET) for _ in range(5))


def sign(payload: dict, ttl=600) -> str:
    body = dict(payload)
    body["_exp"] = time.time() + ttl
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    blob = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    mac = hmac.new(config.SECRET_KEY.encode(), blob.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{blob}.{mac}"


def unsign(token: str):
    try:
        blob, mac = token.split(".", 1)
        expect = hmac.new(config.SECRET_KEY.encode(), blob.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(mac, expect):
            return None
        pad = "=" * (-len(blob) % 4)
        data = json.loads(base64.urlsafe_b64decode(blob + pad))
        if data.get("_exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


# ------------------------------------------------------------------ rate limit
class RateLimiter:
    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key, limit, window):
        """True when the caller is still inside the allowance."""
        now = time.time()
        with self._lock:
            bucket = [t for t in self._hits.get(key, []) if now - t < window]
            bucket.append(now)
            self._hits[key] = bucket
            if len(self._hits) > 20000:
                for k in list(self._hits)[:5000]:
                    self._hits.pop(k, None)
            return len(bucket) <= limit

    def remaining(self, key, limit, window):
        now = time.time()
        with self._lock:
            bucket = [t for t in self._hits.get(key, []) if now - t < window]
            return max(0, limit - len(bucket))


limiter = RateLimiter()


def client_ip(request):
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def fingerprint(request):
    """Weak device fingerprint used only to detect obvious multi-accounting."""
    parts = [
        client_ip(request),
        request.headers.get("User-Agent", "")[:180],
        request.headers.get("Accept-Language", "")[:40],
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]
