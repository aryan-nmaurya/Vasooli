"""Password hashing for operator accounts.

The format is self-describing so cost parameters can be raised later and old hashes
upgraded at the next successful login.  Scrypt is in Python's standard library,
avoiding a second authentication implementation in the frontend or an optional
native dependency in the production image.
"""

import base64
import hashlib
import secrets

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


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
