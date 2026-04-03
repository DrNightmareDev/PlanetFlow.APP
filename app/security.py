import base64
import hashlib
import secrets
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request

from app.config import get_settings

settings = get_settings()

_rate_limit_lock = threading.Lock()
_auth_attempts: dict[tuple[str, str], deque[datetime]] = {}


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _fernet() -> Fernet:
    key_material = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


def encrypt_text(value: str | None) -> str | None:
    if not value:
        return value
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str | None) -> str | None:
    if not value:
        return value
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value


def rate_limit_auth(request: Request, action: str) -> None:
    now = datetime.now(timezone.utc)
    window = timedelta(seconds=settings.auth_rate_limit_window_seconds)
    key = (action, client_ip(request))
    with _rate_limit_lock:
        bucket = _auth_attempts.setdefault(key, deque())
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= settings.auth_rate_limit_max_attempts:
            raise HTTPException(status_code=429, detail="Too many authentication attempts. Please wait and try again.")
        bucket.append(now)


def require_strong_secret_key() -> None:
    weak_defaults = {
        "",
        "change-me",
        "dev-secret",
        "change-me-to-a-secure-random-key-32chars",
        "replace_me_with_a_long_random_secret_key",
    }
    key = settings.secret_key or ""
    looks_random = (
        len(key) >= 32
        and any(ch.islower() for ch in key)
        and any(ch.isupper() for ch in key)
        and any(ch.isdigit() for ch in key)
        and any(not ch.isalnum() for ch in key)
    )
    if key in weak_defaults or not looks_random:
        raise RuntimeError("SECRET_KEY is too weak. Use a long random value with mixed character classes.")


def constant_time_equal(left: str | None, right: str | None) -> bool:
    return secrets.compare_digest(left or "", right or "")
