"""Error classification for the Razorpay client.

Getting this wrong is not cosmetic: a rate limit misread as permanent makes a batch
abandon 58 of 60 invoices in six seconds, which is exactly what happened before this
was fixed.
"""

import pytest
from razorpay.errors import BadRequestError, GatewayError, ServerError

from app.integrations.razorpay_client import (
    RazorpayClient,
    RazorpayPermanentError,
    RazorpayTransientError,
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.integrations.razorpay_client.razorpay.Client", lambda **kw: object())
    c = RazorpayClient(key_id="rzp_test_x", key_secret="s")
    c._min_interval = 0  # don't pace inside unit tests
    return c


def _raise(exc):
    def fn(*a, **k):
        raise exc

    return fn


@pytest.mark.parametrize(
    "message",
    ["Too many requests", "too many requests", "Rate limit exceeded", "TOO MANY REQUESTS"],
)
def test_rate_limit_is_transient_despite_arriving_as_bad_request(client, message):
    """Razorpay labels 429 as BAD_REQUEST_ERROR, so the description is the only signal."""
    with pytest.raises(RazorpayTransientError):
        client._call(_raise(BadRequestError(message)))


@pytest.mark.parametrize(
    "message",
    [
        "amount exceeds maximum amount allowed.",
        "The requested URL was not found on the server.",
        "Recurring digits in customer contact are disallowed",
        "field customer.email is not a valid email",
    ],
)
def test_genuine_bad_requests_stay_permanent(client, message):
    """Retrying these only burns rate limit — the answer will not change."""
    with pytest.raises(RazorpayPermanentError):
        client._call(_raise(BadRequestError(message)))


def test_server_errors_are_transient(client):
    with pytest.raises(RazorpayTransientError):
        client._call(_raise(ServerError("boom")))


def test_gateway_errors_are_transient(client):
    with pytest.raises(RazorpayTransientError):
        client._call(_raise(GatewayError("upstream down")))


def test_network_failures_are_transient(client):
    with pytest.raises(RazorpayTransientError, match="TimeoutError"):
        client._call(_raise(TimeoutError("read timed out")))


def test_pacing_spaces_out_calls(monkeypatch):
    """Every caller is throttled, not just the batch loop."""
    monkeypatch.setattr("app.integrations.razorpay_client.razorpay.Client", lambda **kw: object())
    c = RazorpayClient(key_id="rzp_test_x", key_secret="s")
    c._min_interval = 0.05

    slept: list[float] = []
    monkeypatch.setattr("app.integrations.razorpay_client.time.sleep", slept.append)

    for _ in range(3):
        c._call(lambda: None)

    assert len(slept) >= 2, "consecutive calls should be spaced"
