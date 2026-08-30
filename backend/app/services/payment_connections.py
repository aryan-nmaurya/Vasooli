"""Encrypted Razorpay connection lifecycle for merchant collections."""

import base64
import hashlib
import uuid

from cryptography.fernet import Fernet
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.integrations.razorpay_client import RazorpayClient, get_razorpay_client
from app.models import PaymentConnection


def _fernet() -> Fernet:
    """The key that protects merchant Razorpay credentials at rest.

    This used to fall back to a key derived from `SESSION_SECRET` when
    `CREDENTIAL_ENCRYPTION_KEY` was unset. That fallback was silent, and it was the
    active path everywhere — the variable was not set locally and was not even listed
    in `deploy/.env.example`.

    Two things made it dangerous rather than merely untidy. It reused one secret for
    two unrelated purposes, so a compromise of either widened to both. And rotating
    `SESSION_SECRET` is a routine operation — it is how every session is revoked —
    which would silently re-key every stored credential and leave them permanently
    undecryptable, discovered only the next time a merchant's payment link failed.

    Outside local and test runs the key must now be set explicitly, and startup fails
    if it is missing rather than quietly inventing one.
    """
    configured = settings.credential_encryption_key
    if configured:
        return Fernet(configured.encode())
    if settings.environment in ("staging", "production"):
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY is required outside local and test environments. "
            "Generate one with: python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    # Local and test only, and deliberately derived rather than random: a random key
    # per process would make every stored credential unreadable on the next restart.
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.session_secret.encode()).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def client_for_connection(row: PaymentConnection) -> RazorpayClient:
    """Construct a merchant collection client without exposing stored secrets."""
    if row.mode != "byok" or not row.api_key_id or not row.api_key_secret_encrypted:
        raise ValueError("OAuth collection adapter is not configured")
    return get_razorpay_client(
        key_id=row.api_key_id,
        key_secret=decrypt_secret(row.api_key_secret_encrypted),
    )


def save_connection(
    session: Session,
    merchant_id: uuid.UUID,
    *,
    mode: str,
    provider_account_id: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
    api_key_id: str | None = None,
    api_key_secret: str | None = None,
    scopes: list[str] | None = None,
) -> PaymentConnection:
    row = session.exec(
        select(PaymentConnection).where(PaymentConnection.merchant_id == merchant_id)
    ).first()
    if row is None:
        row = PaymentConnection(merchant_id=merchant_id)
    row.mode = mode
    row.provider_account_id = provider_account_id
    if mode == "oauth":
        row.access_token_encrypted = encrypt_secret(access_token) if access_token else None
        row.refresh_token_encrypted = encrypt_secret(refresh_token) if refresh_token else None
        row.api_key_id = None
        row.api_key_secret_encrypted = None
    else:
        row.access_token_encrypted = None
        row.refresh_token_encrypted = None
        row.api_key_id = api_key_id
        row.api_key_secret_encrypted = encrypt_secret(api_key_secret) if api_key_secret else None
    row.scopes = scopes or row.scopes
    row.status = "connected"
    row.last_verified_at = utcnow()
    row.revoked_at = None
    session.add(row)
    session.flush()
    return row
