"""Security helpers: passwords, login cookies, webhook signatures, encryption, phone numbers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_settings

_ph = PasswordHasher()


# ---------- passwords ----------
def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    return _ph.hash(password)


# A real hash used to waste the same time when the email does not exist (stops guessing which emails exist)
_DUMMY_HASH = _ph.hash("not-a-real-password-just-for-timing")


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


# ---------- login cookie ----------
SESSION_MAX_AGE = 8 * 3600


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="frontdesk-session")


def make_session_cookie(user_id: str, session_version: int) -> tuple[str, str]:
    """Returns (cookie value, csrf token). The csrf token is stored inside the signed cookie."""
    csrf = secrets.token_urlsafe(24)
    return _serializer().dumps({"u": user_id, "v": session_version, "c": csrf}), csrf


def read_session_cookie(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        data = _serializer().loads(value, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) and {"u", "v", "c"} <= set(data) else None


def csrf_ok(session: dict | None, token: str | None) -> bool:
    return bool(session and token and hmac.compare_digest(session["c"], token))


# ---------- webhook signatures ----------
def twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """Twilio signs: full URL + each POST field (sorted by name) as name+value, HMAC-SHA1, base64."""
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(url: str, params: dict[str, str], header: str | None, auth_token: str) -> bool:
    if not header or not auth_token:
        return False
    return hmac.compare_digest(twilio_signature(url, params, auth_token), header)


def verify_shared_secret(header_value: str | None, expected: str) -> bool:
    """For Vapi: it sends the secret in X-Vapi-Secret, or as 'Authorization: Bearer <secret>'."""
    if not expected or not header_value:
        return False
    value = header_value.removeprefix("Bearer ").strip()
    return hmac.compare_digest(value.encode(), expected.encode())


# ---------- encryption of stored tokens ----------
def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt_text(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_text(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


# ---------- 2-step login (TOTP: the 6-digit code from an authenticator app) ----------
def totp_new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_at(secret: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (int.from_bytes(mac[offset:offset + 4], "big") & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_verify(secret: str, code: str | None, *, now: float | None = None, last_step: int = 0) -> int | None:
    """Returns the matching time step (so it can be remembered and never accepted twice), or None."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    current = int((time.time() if now is None else now) // 30)
    for step in (current - 1, current, current + 1):  # allow a little clock difference
        if step > last_step and hmac.compare_digest(_totp_at(secret, step), code):
            return step
    return None


def totp_uri(secret: str, email: str, issuer: str = "AI Front Desk") -> str:
    from urllib.parse import quote

    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}"


# ---------- phone numbers (US only for now) ----------
_NANP = re.compile(r"^\+1[2-9]\d{2}[2-9]\d{6}$")


def normalize_us_phone(raw: str | None) -> str | None:
    """Turn '(512) 555-0147' into '+15125550147'. Return None if it is not a valid US number."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) != 11 or not digits.startswith("1"):
        return None
    candidate = "+" + digits
    return candidate if _NANP.match(candidate) else None


def mask_phone(phone: str | None) -> str:
    """For logs: +15125550147 -> +1512***0147"""
    if not phone or len(phone) < 8:
        return "***"
    return phone[:5] + "***" + phone[-4:]


def speak_phone(phone: str) -> str:
    """Read a number back in a clear way: +15125550147 -> '5 1 2, 5 5 5, 0 1 4 7'."""
    d = phone[-10:]
    return ", ".join(" ".join(part) for part in (d[:3], d[3:6], d[6:]))


# ---------- tiny in-memory rate limiter (one server process) ----------
_hits: dict[str, deque[float]] = defaultdict(deque)


def rate_limited(key: str, limit: int, per_seconds: int, now: float | None = None) -> bool:
    """Returns True when the caller has used up its allowance."""
    now = time.monotonic() if now is None else now
    q = _hits[key]
    while q and q[0] <= now - per_seconds:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def reset_rate_limits() -> None:
    _hits.clear()
