"""Short-lived password re-authentication proofs for sensitive actions."""

import secrets
from datetime import timedelta

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.passwords import verify_live_password
from app.models import ReauthChallenge, User
from app.services.auth import token_hash


def issue_challenge(session: Session, user: User, password: str) -> str:
    if not verify_live_password(password, user.password_hash):
        raise ValueError("Password verification failed")
    raw = secrets.token_urlsafe(32)
    session.add(
        ReauthChallenge(
            user_id=user.id,
            token_hash=token_hash(raw),
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    session.flush()
    return raw


def consume_challenge(session: Session, user: User, raw: str) -> bool:
    row = session.exec(
        select(ReauthChallenge).where(
            ReauthChallenge.user_id == user.id,
            ReauthChallenge.token_hash == token_hash(raw),
        )
    ).first()
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        return False
    row.used_at = utcnow()
    session.add(row)
    return True
