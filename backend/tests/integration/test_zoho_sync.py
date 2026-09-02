"""Zoho Books is the only ERP, and its access tokens expire hourly.

A scheduled sync running every half hour spends most of its life holding a token
that is about to die. If expiry is not handled inside the sync, the first one turns
the connection to `error` and leaves it there until a human presses Refresh — which
is automation that quietly stops being automatic.
"""

import json

import pytest
from sqlmodel import select

from app.integrations.erp import ErpAuthExpiredError, SyncPage, adapter_for_credentials
from app.models import ErpConnection, IntegrationFailure, Merchant
from app.services.erp import sync_connection
from app.services.payment_connections import decrypt_secret, encrypt_secret


@pytest.fixture
def zoho_merchant(session):
    m = Merchant(name="Zoho Ltd", contact_email="ops@zoho.example", is_demo=False, mode="live")
    session.add(m)
    session.commit()
    session.refresh(m)
    return m


@pytest.fixture
def entitled(session, zoho_merchant):
    """Sync is a billable write, so the workspace has to be paid up to run one."""
    from app.models import BillingSubscription
    from app.services.billing import ensure_plans

    plan = next(p for p in ensure_plans(session) if p.slug == "growth")
    session.add(BillingSubscription(merchant_id=zoho_merchant.id, plan_id=plan.id, status="active"))
    session.commit()


@pytest.fixture
def connection(session, zoho_merchant, entitled):
    row = ErpConnection(
        merchant_id=zoho_merchant.id,
        provider="zoho",
        status="connected",
        source_tenant="org-1",
        credentials_encrypted=encrypt_secret(
            json.dumps(
                {
                    "access_token": "expired-token",
                    "refresh_token": "refresh-token",
                    "organization_id": "org-1",
                    "api_domain": "https://www.zohoapis.com",
                }
            )
        ),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_only_zoho_is_a_supported_provider():
    from app.integrations.erp import SUPPORTED_PROVIDERS

    assert {"zoho"} == SUPPORTED_PROVIDERS
    with pytest.raises(ValueError, match="Unsupported ERP provider"):
        adapter_for_credentials("tally", {})
    with pytest.raises(ValueError, match="Unsupported ERP provider"):
        adapter_for_credentials("custom", {})


def test_an_expired_token_is_refreshed_and_the_sync_completes(session, connection, monkeypatch):
    """The whole point: expiry is routine, so it must not surface as a failure."""
    from app.services import erp as erp_mod

    calls: list[str] = []

    class Adapter:
        def __init__(self, credentials, **_kw):
            self.token = credentials["access_token"]

        def fetch_invoices(self, *, cursor, limit):
            calls.append(self.token)
            if self.token == "expired-token":
                raise ErpAuthExpiredError("Zoho access token expired")
            return SyncPage(records=[], next_cursor=None, has_more=False)

    class Tokens:
        access_token = "fresh-token"
        refresh_token = "rotated-refresh"
        api_domain = "https://www.zohoapis.com"

    monkeypatch.setattr(erp_mod, "adapter_for_credentials", lambda p, c, **k: Adapter(c))
    monkeypatch.setattr(erp_mod, "refresh_zoho_token", lambda _rt: Tokens())

    run = sync_connection(session, connection)

    assert calls == ["expired-token", "fresh-token"], "it must retry once with the new token"
    assert run.status == "completed"
    assert connection.status == "healthy"

    stored = json.loads(decrypt_secret(connection.credentials_encrypted))
    assert stored["access_token"] == "fresh-token", "the new token must be persisted"
    assert stored["refresh_token"] == "rotated-refresh"


def test_a_refresh_token_that_zoho_does_not_rotate_is_kept(session, connection, monkeypatch):
    """Zoho does not reissue the refresh token every time.

    Overwriting it with an empty value would leave the connection unrefreshable on
    the next expiry — a failure that appears an hour later, far from its cause.
    """
    from app.services import erp as erp_mod

    class Adapter:
        def __init__(self, credentials, **_kw):
            self.token = credentials["access_token"]

        def fetch_invoices(self, *, cursor, limit):
            if self.token == "expired-token":
                raise ErpAuthExpiredError("expired")
            return SyncPage(records=[], next_cursor=None, has_more=False)

    class Tokens:
        access_token = "fresh-token"
        refresh_token = None
        api_domain = None

    monkeypatch.setattr(erp_mod, "adapter_for_credentials", lambda p, c, **k: Adapter(c))
    monkeypatch.setattr(erp_mod, "refresh_zoho_token", lambda _rt: Tokens())

    sync_connection(session, connection)

    stored = json.loads(decrypt_secret(connection.credentials_encrypted))
    assert stored["refresh_token"] == "refresh-token"


def test_it_refreshes_at_most_once(session, connection, monkeypatch):
    """A freshly minted token being rejected is not expiry — it is a revoked grant.

    Retrying that in a loop is a slower way to fail, and it burns Zoho's rate limit
    for every other merchant on the same platform credentials.
    """
    from app.services import erp as erp_mod

    attempts: list[str] = []

    class AlwaysExpired:
        def __init__(self, credentials, **_kw):
            self.token = credentials["access_token"]

        def fetch_invoices(self, *, cursor, limit):
            attempts.append(self.token)
            raise ErpAuthExpiredError("still rejected")

    class Tokens:
        access_token = "fresh-token"
        refresh_token = "refresh-token"
        api_domain = None

    monkeypatch.setattr(erp_mod, "adapter_for_credentials", lambda p, c, **k: AlwaysExpired(c))
    monkeypatch.setattr(erp_mod, "refresh_zoho_token", lambda _rt: Tokens())

    run = sync_connection(session, connection)

    assert len(attempts) == 2, "one original attempt and exactly one retry"
    assert run.status == "failed"
    assert run.error


def test_a_connection_with_no_refresh_token_fails_loudly(session, connection, monkeypatch):
    """Nothing to refresh with, so the merchant must be told to reconnect."""
    from app.services import erp as erp_mod

    connection.credentials_encrypted = encrypt_secret(
        json.dumps({"access_token": "expired-token", "organization_id": "org-1"})
    )
    session.add(connection)
    session.commit()

    class Adapter:
        def __init__(self, credentials, **_kw):
            pass

        def fetch_invoices(self, *, cursor, limit):
            raise ErpAuthExpiredError("Zoho access token expired")

    monkeypatch.setattr(erp_mod, "adapter_for_credentials", lambda p, c, **k: Adapter(c))

    def must_not_refresh(_rt):
        raise AssertionError("cannot refresh without a refresh token")

    monkeypatch.setattr(erp_mod, "refresh_zoho_token", must_not_refresh)

    run = sync_connection(session, connection)

    assert run.status == "failed"
    assert connection.status == "error"
    failures = session.exec(
        select(IntegrationFailure).where(IntegrationFailure.connection_id == connection.id)
    ).all()
    assert len(failures) == 1
    assert failures[0].next_retry_at is not None


def test_a_sync_failure_never_records_an_empty_message(session, connection, monkeypatch):
    """An error with no text tells the merchant nothing and the operator less."""
    from app.services import erp as erp_mod

    class BoomError(Exception):
        def __str__(self):
            return ""

    def explode(*_a, **_k):
        raise BoomError()

    monkeypatch.setattr(erp_mod, "adapter_for_credentials", explode)

    run = sync_connection(session, connection)

    assert run.status == "failed"
    assert run.error == "BoomError"
