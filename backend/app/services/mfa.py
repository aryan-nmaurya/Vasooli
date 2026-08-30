"""Dependency-free TOTP enrollment and verification for live accounts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time as wall_clock


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def verify_code(secret: str, code: str, *, timestamp: int | None = None) -> bool:
    cleaned = code.replace(" ", "").strip()
    if len(cleaned) != 6 or not cleaned.isdigit():
        return False
    padded = secret + "=" * (-len(secret) % 8)
    try:
        key = base64.b32decode(padded, casefold=True)
    except (ValueError, base64.binascii.Error):
        return False
    # MFA expiry is real wall time, never the demo business clock.
    counter = int((timestamp or int(wall_clock.time())) // 30)
    for offset in (-1, 0, 1):
        digest = hmac.new(key, struct.pack(">Q", counter + offset), hashlib.sha1).digest()
        index = digest[-1] & 0x0F
        value = (struct.unpack(">I", digest[index : index + 4])[0] & 0x7FFFFFFF) % 1_000_000
        if hmac.compare_digest(f"{value:06d}", cleaned):
            return True
    return False


def provisioning_uri(secret: str, email: str) -> str:
    issuer = "Vasooli"
    return f"otpauth://totp/{issuer}:{email}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
