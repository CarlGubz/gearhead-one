"""Password hashing, opaque tokens and password policy."""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id with the library's current safe defaults

# Verified against when the e-mail is unknown so login timing does not reveal
# which addresses are registered.
_DUMMY_HASH = _hasher.hash("gearhead-timing-equaliser")

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

_COMMON = {
    "password", "password1", "password12", "password123", "1234567890", "12345678910",
    "qwertyuiop", "qwerty12345", "iloveyou12", "letmein1234", "welcome123", "admin12345",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """Constant-ish time check; passing None burns the same CPU as a real hash."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problem(password: str, email: str = "") -> Optional[str]:
    """Return a human-readable reason the password is unacceptable, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    low = password.lower()
    if low in _COMMON or len(set(low)) < 4:
        return "That password is too easy to guess. Try a longer phrase."
    local = email.split("@")[0].lower() if email else ""
    if local and len(local) >= 4 and local in low:
        return "Password must not contain your e-mail name."
    return None


def new_token() -> str:
    """256 bits of randomness, URL-safe. Only its hash is ever stored."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
