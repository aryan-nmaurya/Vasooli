"""Reviewer access may run in production, but only while it is genuinely read-only.

The button hands a session to anyone who clicks it, with no credential. That is safe
because the account behind it is an `auditor` and `app.api.deps` rejects every
non-GET request from an auditor. If the account were ever given a writing role, the
UI would still call it a read-only demo while granting write access to the operator
console — so the app refuses to start instead.
"""

import pytest

from app.core.config import Settings
from app.models import OperatorAccount
from app.services.auth import verify_reviewer_account


def _production_settings(**overrides):
    base = dict(
        environment="production",
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        admin_api_key="a" * 40,
        session_secret="s" * 40,
        credential_encryption_key="k" * 44,
        razorpay_key_id="rzp_test_demo",
        razorpay_key_secret="demo-secret",
        razorpay_webhook_secret="demo-webhook",
        email_dry_run=True,
        allow_simulated_replies=False,
        demo_controls_enabled=False,
        live_registration_enabled=False,
        scheduler_enabled=False,
    )
    base.update(overrides)
    return base


def test_reviewer_access_is_permitted_in_production():
    """It only reads, and forbidding it also forbade having a public demo."""
    Settings(**_production_settings(reviewer_access_enabled=True)).assert_production_safe()


@pytest.mark.parametrize("flag", ["allow_simulated_replies", "demo_controls_enabled"])
def test_the_writing_demo_flags_are_still_forbidden(flag):
    """These two write. A simulated reply fabricates a customer statement in the
    audit trail; demo controls move a clock the live recovery cycle reads."""
    cfg = Settings(**_production_settings(**{flag: True}))
    with pytest.raises(RuntimeError, match="must be false in production"):
        cfg.assert_production_safe()


def _reviewer(session, role: str, *, active: bool = True):
    account = OperatorAccount(
        username="reviewer",
        display_name="Reviewer",
        password_hash="x",
        role=role,
        is_active=active,
    )
    session.add(account)
    session.commit()
    return account


def test_a_writing_reviewer_account_is_reported(session):
    """The whole read-only guarantee rests on this role, so it is checked not trusted.

    Startup logs this rather than crashing: the request path already fails closed
    twice over, and taking live merchants offline for a broken demo door would trade
    a real outage for a risk already covered.
    """
    _reviewer(session, "admin")
    with pytest.raises(RuntimeError) as exc:
        verify_reviewer_account()
    assert "not 'auditor'" in str(exc.value)
    assert "read-only" in str(exc.value)


def test_a_missing_reviewer_account_is_reported(session):
    """The old failure mode: the flag on, no account, and a button that only 404s."""
    with pytest.raises(RuntimeError, match="no operator account named"):
        verify_reviewer_account()


def test_an_inactive_reviewer_account_is_reported(session):
    _reviewer(session, "auditor", active=False)
    with pytest.raises(RuntimeError, match="inactive"):
        verify_reviewer_account()


def test_a_correct_auditor_account_passes(session):
    _reviewer(session, "auditor")
    verify_reviewer_account()  # must not raise
