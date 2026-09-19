"""Password hashing (Argon2id via argon2-cffi) and opaque session tokens.

No custom cryptography: Argon2id with the library's recommended parameters,
``secrets`` for tokens, and HMAC-SHA256 so only token digests are stored.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import settings

_hasher = PasswordHasher()  # Argon2id, RFC 9106 low-memory profile defaults
# Verified against when the email is unknown, so timing does not reveal accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}$")
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
COMMON_PASSWORDS = {
    "password", "password1", "password123", "1234567890", "qwertyuiop", "letmein123",
    "iloveyou12", "admin12345", "welcome123", "cermat1234", "abcdefghij", "0123456789",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, email: str) -> list[str]:
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        problems.append("Choose a less common password.")
    if email and password.lower() == email.lower():
        problems.append("The password must not be your email address.")
    return problems


def normalise_email(email: str) -> str:
    return email.strip().lower()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def digest(token: str, purpose: str = "session") -> str:
    return hmac.new(
        settings.secret_key.encode(), f"{purpose}:{token}".encode(), hashlib.sha256
    ).hexdigest()


def csrf_for(session_token: str) -> str:
    """Deterministic per-session CSRF token (synchroniser pattern, never stored in plain)."""
    return digest(session_token, "csrf-token")


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
