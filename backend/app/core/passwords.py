"""Password hashing for operator accounts.

The format is self-describing so cost parameters can be raised later and old hashes
upgraded at the next successful login.  Scrypt is in Python's standard library,
avoiding a second authentication implementation in the frontend or an optional
native dependency in the production image.
"""

import base64
import contextlib
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32

# OWASP's Argon2id baseline: 19 MiB memory, two iterations, one lane. Kept separate
# from demo scrypt hashes so the frozen operator credentials do not change format.
_LIVE_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=SCRYPT_DKLEN,
    )


def hash_password(password: str) -> str:
    """Return a salted, versioned scrypt password hash."""
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return "$".join(
        (
            "scrypt-v1",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password, failing closed for corrupt or unknown hash formats."""
    try:
        version, n_raw, r_raw, p_raw, salt_raw, digest_raw = encoded.split("$")
        if version != "scrypt-v1":
            return False
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
        actual = _derive(
            password,
            salt,
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected)


def perform_dummy_password_check(password: str) -> None:
    """Spend the same order of work when a username does not exist.

    Without this, username enumeration is possible from login response timing even
    though the HTTP status and body are deliberately identical.
    """
    _derive(password, b"vasooli-login-dummy", n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)


#: A real Argon2id hash of a value nobody can present, so the unknown-email branch of
#: live login does the same work as the known-email branch. Computed once at import.
_DUMMY_LIVE_HASH = _LIVE_HASHER.hash("vasooli-live-login-dummy")


def perform_dummy_live_password_check(password: str) -> None:
    """The Argon2id equivalent, for the live login's unknown-email branch.

    `perform_dummy_password_check` spends scrypt work and is right for operator login.
    Live identities are Argon2id, which is a different cost, so reusing the scrypt one
    would leave a timing gap of a different shape rather than closing it. Verifying
    against a fixed hash costs what a real verification costs.
    """
    # A mismatch is the expected outcome; the work, not the answer, is the point.
    with contextlib.suppress(Exception):
        _LIVE_HASHER.verify(_DUMMY_LIVE_HASH, password)


def hash_live_password(password: str) -> str:
    """Argon2id for live email identities."""

    return _LIVE_HASHER.hash(password)


def verify_live_password(password: str, encoded: str) -> bool:
    try:
        return _LIVE_HASHER.verify(encoded, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def live_password_needs_rehash(encoded: str) -> bool:
    try:
        return _LIVE_HASHER.check_needs_rehash(encoded)
    except InvalidHashError:
        return True
