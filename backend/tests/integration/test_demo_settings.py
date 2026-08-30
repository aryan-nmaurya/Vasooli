"""The simulated clock. Demo controls.

The cadence fires at 3, 10 and 21 days overdue, which no reviewer will wait for. These
pin the two things that make compressing it safe rather than merely convenient: it is
off unless a deployment opts in, and moving it is audited.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core import clock, runtime
from app.core.config import settings
from app.main import create_app
from app.models import AuditAction, AuditLog, DemoSettings
from app.services import demo_control


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_module_clock():
    """The offset lives in module state, so a leaked value would bleed across tests."""
    clock.set_runtime_offset(0)
    runtime.set_email_redirect_override(None)
    yield
    clock.set_runtime_offset(0)
    runtime.set_email_redirect_override(None)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "demo_controls_enabled", True)


# ===========================================================================
# Off by default.
# ===========================================================================


def test_the_clock_is_disabled_unless_a_deployment_opts_in(session, monkeypatch):
    """A real multi-merchant deployment must not be nudgeable into a fictional
    present by an endpoint someone forgot was enabled."""
    monkeypatch.setattr(settings, "demo_controls_enabled", False)
    with pytest.raises(demo_control.DemoControlsDisabledError):
        demo_control.advance(session, days=7, actor="human:test")


def test_the_endpoint_refuses_when_disabled(api, session, monkeypatch):
    monkeypatch.setattr(settings, "demo_controls_enabled", False)
    res = api.post(
        "/api/demo/advance",
        json={"days": 7, "run_cycle": False},
        headers={"X-Admin-Key": settings.admin_api_key},
    )
    assert res.status_code == 409


def test_disabled_deployments_report_real_time(api, session, monkeypatch):
    monkeypatch.setattr(settings, "demo_controls_enabled", False)
    body = api.get("/api/demo/clock", headers={"X-Admin-Key": settings.admin_api_key}).json()
    assert body["enabled"] is False
    assert body["offset_days"] == 0
    assert body["simulated_date"] == body["real_date"]


# ===========================================================================
# Moving time.
# ===========================================================================


def test_advancing_moves_the_business_clock(session, enabled):
    """The whole point: `days_overdue` has to grow, or the cadence never fires."""
    from datetime import UTC, datetime, timedelta

    due = datetime.now(UTC) - timedelta(days=1)
    assert clock.days_overdue(due) == 1

    demo_control.advance(session, days=7, actor="human:test")
    assert clock.days_overdue(due) == 8


def test_advancing_is_audited_with_who_and_how_much(session, enabled):
    """The clock changes what the system believes 'now' is, and every decision below
    it inherits that. A trail recording the consequences but not the cause would be
    worse than none."""
    demo_control.advance(session, days=3, actor="human:reviewer@example.com")

    entry = next(
        a
        for a in session.exec(select(AuditLog)).all()
        if a.action == AuditAction.DEMO_CLOCK_ADVANCED
    )
    assert entry.actor == "human:reviewer@example.com"
    assert entry.detail["days"] == 3
    assert entry.detail["offset_before"] == 0
    assert entry.detail["offset_after"] == 3


def test_advancing_accumulates(session, enabled):
    demo_control.advance(session, days=3, actor="human:test")
    row = demo_control.advance(session, days=4, actor="human:test")
    assert row.offset_days == 7
    assert clock.runtime_offset() == 7


def test_reset_returns_to_real_time_and_is_audited(session, enabled):
    demo_control.advance(session, days=10, actor="human:test")
    demo_control.reset(session, actor="human:test")

    assert clock.runtime_offset() == 0
    assert session.get(DemoSettings, 1).offset_days == 0
    assert any(
        a.action == AuditAction.DEMO_CLOCK_RESET for a in session.exec(select(AuditLog)).all()
    )


@pytest.mark.parametrize("days", [0, -1, demo_control.MAX_ADVANCE_DAYS + 1])
def test_an_out_of_range_jump_is_refused(session, enabled, days):
    """A stuck auto-advance must not be able to walk the ledger years forward."""
    with pytest.raises(ValueError):
        demo_control.advance(session, days=days, actor="human:test")


def test_the_offset_survives_a_restart(session, enabled):
    """Module state is rehydrated from the row at startup. Without that, a restart
    mid-review silently rewinds the demo and the cadence appears to un-fire."""
    demo_control.advance(session, days=9, actor="human:test")

    clock.set_runtime_offset(0)  # simulate a fresh process
    assert clock.runtime_offset() == 0

    assert demo_control.load_into_clock(session) == 9
    assert clock.runtime_offset() == 9


def test_a_disabled_deployment_loads_zero_even_with_a_stale_row(session, enabled, monkeypatch):
    """Turning the flag off must actually turn the clock off, not leave whatever the
    row last held in force."""
    demo_control.advance(session, days=12, actor="human:test")
    monkeypatch.setattr(settings, "demo_controls_enabled", False)

    assert demo_control.load_into_clock(session) == 0
    assert clock.runtime_offset() == 0


# ===========================================================================
# Where reminder mail goes. Reviewer settings.
# ===========================================================================


def test_a_reviewer_can_point_mail_at_their_own_inbox(session, enabled):
    """The only way to exercise the inbound path without deployment access: receive a
    real reminder, then reply to it."""
    from app.services.messaging import resolve_recipient

    demo_control.set_email_redirect(session, address="reviewer@example.com", actor="human:reviewer")
    to, intended_for = resolve_recipient("customer@invented-domain.example.com")

    assert to == "reviewer@example.com"
    assert intended_for == "customer@invented-domain.example.com"


def test_the_reply_from_that_inbox_is_accepted_as_the_operator(session, enabled):
    """The send path and the inbound path must agree on the address, or a reviewer
    receives a reminder and then has their reply refused as a stranger's."""
    from app.core.runtime import effective_email_redirect

    demo_control.set_email_redirect(session, address="reviewer@example.com", actor="human:reviewer")
    assert effective_email_redirect() == "reviewer@example.com"


def test_clearing_falls_back_and_never_disables_redirection(session, enabled, monkeypatch):
    """The safety property that makes this endpoint safe to expose at all.

    No value accepted here may result in mail going to the customer addresses in the
    seeded ledger — clearing returns to the deployment default, it does not turn
    redirection off.
    """
    from app.core.runtime import effective_email_redirect

    monkeypatch.setattr(settings, "email_redirect_to", "ops@vasooli.test")
    demo_control.set_email_redirect(session, address="reviewer@example.com", actor="human:x")
    demo_control.set_email_redirect(session, address=None, actor="human:x")

    assert effective_email_redirect() == "ops@vasooli.test"


@pytest.mark.parametrize("bad", ["notanemail", "@nolocalpart.com", "x" * 300])
def test_an_obviously_invalid_address_is_refused(session, enabled, bad):
    """A typo here would silently swallow every reminder for the rest of the session."""
    with pytest.raises(ValueError):
        demo_control.set_email_redirect(session, address=bad, actor="human:x")


def test_changing_the_destination_is_audited(session, enabled):
    """Outbound mail is the system's one side effect on the outside world; where it
    was pointed, and by whom, is part of the record."""
    demo_control.set_email_redirect(session, address="reviewer@example.com", actor="human:reviewer")
    entry = next(
        a
        for a in session.exec(select(AuditLog)).all()
        if a.action == AuditAction.DEMO_EMAIL_REDIRECTED
    )
    assert entry.actor == "human:reviewer"
    assert entry.detail["to"] == "reviewer@example.com"


def test_the_override_survives_a_restart(session, enabled):
    demo_control.set_email_redirect(session, address="reviewer@example.com", actor="human:x")
    runtime.set_email_redirect_override(None)  # simulate a fresh process

    demo_control.load_into_clock(session)
    assert runtime.email_redirect_override() == "reviewer@example.com"


def test_a_disabled_deployment_ignores_a_stale_override(session, enabled, monkeypatch):
    demo_control.set_email_redirect(session, address="reviewer@example.com", actor="human:x")
    monkeypatch.setattr(settings, "demo_controls_enabled", False)

    demo_control.load_into_clock(session)
    assert runtime.email_redirect_override() is None
