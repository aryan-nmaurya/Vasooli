"""Email delivery, the redirect guard, and provider error handling. Doc §10."""

import pytest

from app.core.config import settings
from app.integrations.email.base import SendResult
from app.services.messaging import _subject_for, render_html, resolve_recipient


@pytest.fixture
def redirect_off(monkeypatch):
    monkeypatch.setattr(settings, "email_redirect_to", None, raising=False)


@pytest.fixture
def redirect_on(monkeypatch):
    monkeypatch.setattr(settings, "email_redirect_to", "me@example.com", raising=False)


# ===========================================================================
# The redirect guard. The synthetic ledger has 52 invented domains.
# ===========================================================================


def test_redirect_sends_to_the_operator_not_the_customer(redirect_on):
    to, intended = resolve_recipient("neha@shakti.supplies.example.com")
    assert to == "me@example.com"
    assert intended == "neha@shakti.supplies.example.com"


def test_without_a_redirect_mail_goes_to_the_customer(redirect_off):
    to, intended = resolve_recipient("neha@example.com")
    assert to == "neha@example.com"
    assert intended is None


def test_the_subject_shows_who_it_was_meant_for(redirect_on):
    subject = _subject_for("Invoice INV-1 overdue", "neha@shakti.example.com")
    assert subject.startswith("[→ neha@shakti.example.com]")


def test_an_unredirected_subject_is_untouched():
    assert _subject_for("Invoice INV-1 overdue", None) == "Invoice INV-1 overdue"


def test_live_sending_is_refused_without_a_redirect(monkeypatch):
    """Turning off dry-run must be a deliberate act with a stated destination."""
    monkeypatch.setattr(settings, "email_dry_run", False, raising=False)
    monkeypatch.setattr(settings, "email_redirect_to", None, raising=False)
    with pytest.raises(RuntimeError, match="EMAIL_REDIRECT_TO"):
        settings.assert_safe_to_send()


def test_dry_run_needs_no_redirect(monkeypatch):
    monkeypatch.setattr(settings, "email_dry_run", True, raising=False)
    monkeypatch.setattr(settings, "email_redirect_to", None, raising=False)
    settings.assert_safe_to_send()  # must not raise


# ===========================================================================
# Rendering.
# ===========================================================================


def test_html_escapes_customer_supplied_text():
    html = render_html("Hello <script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_paragraphs_survive_the_conversion():
    html = render_html("First line.\n\nSecond line.")
    assert html.count("<p") == 2


# ===========================================================================
# Provider error classification.
# ===========================================================================


class FakeResponse:
    def __init__(self, status_code, body="err"):
        self.status_code = status_code
        self.text = body

    def json(self):
        return {"id": "msg_1"}


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(200, False), (429, True), (500, True), (503, True), (401, False), (422, False)],
)
def test_which_failures_are_worth_retrying(monkeypatch, status_code, retryable):
    """Retrying a 4xx burns quota for an answer that will not change."""
    import app.integrations.email.resend_client as mod

    monkeypatch.setattr(mod.httpx, "post", lambda *a, **k: FakeResponse(status_code))
    result = mod.ResendProvider(api_key="re_test").send(
        to="a@example.com", subject="s", html="<p>h</p>", text="t"
    )
    assert isinstance(result, SendResult)
    if status_code < 300:
        assert result.sent is True
    else:
        assert result.sent is False
        assert result.retryable is retryable


def test_a_network_error_is_retryable(monkeypatch):
    import httpx

    import app.integrations.email.resend_client as mod

    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(mod.httpx, "post", boom)
    result = mod.ResendProvider(api_key="re_test").send(
        to="a@example.com", subject="s", html="<p>h</p>", text="t"
    )
    assert result.sent is False
    assert result.retryable is True
