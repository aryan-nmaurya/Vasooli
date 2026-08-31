"""Expired ephemeral auth material is pruned; evidence is not.

Sessions, refresh tokens and one-time states are written on every login and every
connect attempt, read on the authenticated hot path, and were never deleted. These
pin both halves of the rule: expired scratch state goes, and the rows that constitute
evidence stay even when they carry an `expires_at`.
"""

from datetime import timedelta

from sqlalchemy import text
from sqlmodel import select

from app.core.clock import utcnow
from app.models import SuppressionEntry
from app.services.retention import (
    ONE_TIME_GRACE_DAYS,
    SESSION_GRACE_DAYS,
    prune_expired,
)


def _a_user(session):
    """OAuth state is bound to the person who started the connect flow."""
    from app.core.passwords import hash_password
    from app.models import User

    user = session.exec(select(User)).first()
    if user is None:
        user = User(
            email="connector@merchant.example",
            password_hash=hash_password("AVeryStrongPassphrase123!"),
            status="active",
            is_email_verified=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def _insert_oauth_state(session, merchant, *, expires_at):
    user = _a_user(session)
    session.exec(
        text(
            """
            INSERT INTO oauth_states
                (id, merchant_id, user_id, provider, state_hash, redirect_uri,
                 metadata, expires_at, created_at)
            VALUES (gen_random_uuid(), :m, :u, 'razorpay', :h, 'https://x.test/cb',
                    '{}', :e, now())
            """
        ).bindparams(m=merchant.id, u=user.id, h=f"hash-{expires_at.timestamp()}", e=expires_at)
    )
    session.commit()


def _oauth_state_count(session) -> int:
    return session.exec(text("SELECT count(*) FROM oauth_states")).one()[0]


def test_a_state_past_its_grace_window_is_deleted(session, merchant):
    _insert_oauth_state(
        session, merchant, expires_at=utcnow() - timedelta(days=ONE_TIME_GRACE_DAYS + 1)
    )
    assert _oauth_state_count(session) == 1

    report = prune_expired(session)
    session.commit()

    assert report.deleted.get("oauth_states") == 1
    assert _oauth_state_count(session) == 0


def test_a_recently_expired_state_is_kept_for_the_grace_window(session, merchant):
    """A security question asked next week is unanswerable if the rows went
    the moment they expired."""
    _insert_oauth_state(session, merchant, expires_at=utcnow() - timedelta(hours=1))

    prune_expired(session)
    session.commit()

    assert _oauth_state_count(session) == 1


def test_an_unexpired_state_is_never_touched(session, merchant):
    _insert_oauth_state(session, merchant, expires_at=utcnow() + timedelta(hours=1))

    prune_expired(session)
    session.commit()

    assert _oauth_state_count(session) == 1


def test_expired_suppressions_are_evidence_and_survive(session, merchant):
    """An expired suppression records that this address once bounced or opted out.

    It carries an `expires_at`, so a naive "delete anything expired" rule would take
    it — and the merchant would lose the reason they stopped contacting someone.
    """
    entry = SuppressionEntry(
        merchant_id=merchant.id,
        email="stopped@buyer.example.com",
        reason="hard_bounce",
        expires_at=utcnow() - timedelta(days=SESSION_GRACE_DAYS + 400),
    )
    session.add(entry)
    session.commit()

    prune_expired(session)
    session.commit()

    survivors = session.exec(
        select(SuppressionEntry).where(SuppressionEntry.merchant_id == merchant.id)
    ).all()
    assert len(survivors) == 1


def test_pruning_nothing_reports_nothing(session):
    report = prune_expired(session)
    assert report.total == 0
    assert report.deleted == {}


def test_the_batch_limit_bounds_a_single_pass(session, merchant):
    """An unbounded DELETE on a table left unpruned for a year takes a long lock and
    can time out mid-statement — a worse outage than the bloat it fixes."""
    old = utcnow() - timedelta(days=ONE_TIME_GRACE_DAYS + 5)
    for offset in range(5):
        _insert_oauth_state(session, merchant, expires_at=old + timedelta(seconds=offset))

    first = prune_expired(session, batch_limit=2)
    session.commit()
    assert first.deleted.get("oauth_states") == 2
    assert _oauth_state_count(session) == 3

    second = prune_expired(session, batch_limit=10)
    session.commit()
    assert second.deleted.get("oauth_states") == 3
    assert _oauth_state_count(session) == 0
