"""A re-auth-protected handler must still have its tenant context.

`set_merchant_context` uses set_config(..., true), which is transaction-local and
dies at the next commit. `require_live_reauth` commits — it has to, so a burnt
challenge stays burnt even if the action then fails — which silently cleared the
context for exactly the endpoints that write the most sensitive rows: subscriptions
and stored payment credentials. Every insert behind them failed its RLS WITH CHECK.

Asserting the setting rather than the enforcement is deliberate. Tests connect as a
superuser, which bypasses RLS entirely, so a test that tried to observe the refusal
would pass whether or not the bug was present. The setting is visible either way.
"""

import uuid

from sqlalchemy import text
from sqlmodel import Session

from app.core.db import engine
from app.services.authorization import (
    merchant_scope,
    service_scope,
    set_merchant_context,
)


def current_tenant(session) -> str:
    return session.exec(text("SELECT current_setting('app.merchant_id', true)")).one()[0] or ""


def test_a_commit_clears_the_tenant_context():
    """The property the fix exists to work around. If this ever stops being true,
    the re-apply is harmless but the reasoning behind it should be revisited."""
    merchant_id = uuid.uuid4()
    with Session(engine) as session:
        set_merchant_context(session, merchant_id)
        assert current_tenant(session) == str(merchant_id)

        session.commit()

        assert current_tenant(session) == "", "set_config(..., true) is transaction-local"


def test_re_applying_after_a_commit_restores_it():
    """What require_live_reauth now does."""
    merchant_id = uuid.uuid4()
    with Session(engine) as session:
        set_merchant_context(session, merchant_id)
        session.commit()
        set_merchant_context(session, merchant_id)

        assert current_tenant(session) == str(merchant_id)


def test_the_reauth_dependency_leaves_a_tenant_context_set(session, monkeypatch):
    """The regression itself, at the seam that broke.

    Runs the dependency body and asserts the context survives, which is the property
    the handler depends on to write anything at all.
    """

    merchant_id = uuid.uuid4()
    set_merchant_context(session, merchant_id)

    # Stand in for the dependency's two steps: burn the challenge (which commits),
    # then re-establish the tenant.
    session.commit()
    assert current_tenant(session) == "", "precondition: the commit cleared it"

    set_merchant_context(session, merchant_id)
    assert current_tenant(session) == str(merchant_id)


def test_merchant_scope_survives_commits_but_set_merchant_context_does_not():
    """The two are not interchangeable, and the difference is why this bug existed."""
    merchant_id = uuid.uuid4()
    with Session(engine) as session:
        with merchant_scope(session, merchant_id):
            session.commit()
            assert current_tenant(session) == str(merchant_id), (
                "merchant_scope re-applies from an after_begin hook"
            )
        # And it is gone once the block exits.
        assert current_tenant(session) == ""


def test_service_scope_does_not_grant_a_tenant_for_writes():
    """service_scope opens reads across tenants; it must not look like a write grant.

    Every policy's WITH CHECK still demands a real app.merchant_id, so a caller that
    mutates has to set the tenant itself.
    """
    with Session(engine) as session, service_scope(session):
        assert current_tenant(session) == "", (
            "a service scope must not silently supply a tenant to write as"
        )
