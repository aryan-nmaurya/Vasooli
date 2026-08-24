"""AI timeout, retry, and failover. P1.

LLM_TIMEOUT_SECONDS and LLM_MAX_RETRIES existed in config and were never read by the
client. A hung model call therefore blocked the recovery cycle — which runs
synchronously — indefinitely, and a scheduled run that never returns is
indistinguishable from one that never ran.
"""

import pytest

from app.ai.client import LLMClient, LLMUnavailableError, _is_retryable
from app.ai.schemas import DiagnosisResponse
from app.core.config import settings


@pytest.fixture
def client(monkeypatch):
    """A client with a plausible key, and no real sleeping between retries."""
    monkeypatch.setattr("app.ai.client.time.sleep", lambda _: None)
    return LLMClient(api_key="real-looking-key")


def valid_response() -> DiagnosisResponse:
    from app.core.constants import ReasonCategory

    return DiagnosisResponse(
        category=ReasonCategory.OVERSIGHT, explanation="clean payer", confidence=0.8
    )


# ===========================================================================
# Which faults are worth retrying.
# ===========================================================================


@pytest.mark.parametrize(
    "message",
    [
        "ServerError: 503 UNAVAILABLE",
        "ServerError: 500 internal error",
        "ServerError: 502 bad gateway",
        "ConnectionError: connection reset",
    ],
)
def test_a_blip_is_retried_on_the_same_model(message):
    """Short-lived faults clear in a second; failing over on the first would spend the
    primary model's better output on a problem that fixed itself."""
    assert _is_retryable(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "ClientError: 429 RESOURCE_EXHAUSTED",
        "rate limit exceeded",
        "quota exceeded for metric generate_content_free_tier_requests",
    ],
)
def test_quota_exhaustion_fails_over_instead_of_retrying(message):
    """Quota is counted PER MODEL, so a 429 on the primary says nothing about the
    fallback. Measured on the live API: Gemini's 429 carries `retryDelay: 12s`, so a
    sub-second retry cannot succeed — it only spends time proving that."""
    assert _is_retryable(message) is False


@pytest.mark.parametrize(
    "message",
    ["ServerError: 504 DEADLINE_EXCEEDED", "ReadTimeout: request timed out", "DeadlineExceeded"],
)
def test_a_timeout_fails_over_instead_of_retrying(message):
    """Each timeout attempt burns the full configured timeout before admitting defeat.

    Measured on the live API: two retries at 20s each turned one call into 64 seconds,
    which over a cycle of 8 invoices is a quarter of an hour.
    """
    assert _is_retryable(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "NotFound: model gemini-99-flash does not exist",
        "InvalidArgument: malformed request",
        "PermissionDenied: API key lacks access",
    ],
)
def test_permanent_faults_are_not_retryable(message):
    """Retrying these burns quota for an answer that cannot change."""
    assert _is_retryable(message) is False


# ===========================================================================
# Retry behaviour on one model.
# ===========================================================================


def test_a_transient_fault_is_retried_on_the_same_model(client, monkeypatch):
    calls = []

    def flaky(model, prompt, response_model):
        calls.append(model)
        if len(calls) < 2:
            raise LLMUnavailableError("ServerError: 503 UNAVAILABLE")
        return valid_response()

    monkeypatch.setattr(client, "_generate_once", flaky)
    result = client.generate_structured(prompt="x", response_model=DiagnosisResponse, task="t")

    assert result.ok is True
    assert calls == [settings.gemini_primary_model] * 2, "retried, not failed over"
    assert result.degraded is False, "the primary answered in the end"


def test_retries_are_bounded_by_the_configured_limit(client, monkeypatch):
    calls = []

    def always_down(model, prompt, response_model):
        calls.append(model)
        raise LLMUnavailableError("503 UNAVAILABLE")

    monkeypatch.setattr(client, "_generate_once", always_down)
    client.generate_structured(prompt="x", response_model=DiagnosisResponse, task="t")

    per_model = settings.llm_max_retries + 1
    assert len(calls) == per_model * 2, "each of two models tried its full allowance"


def test_a_permanent_fault_fails_over_immediately(client, monkeypatch):
    calls = []

    def bad_model(model, prompt, response_model):
        calls.append(model)
        if model == settings.gemini_primary_model:
            raise LLMUnavailableError("NotFound: model does not exist")
        return valid_response()

    monkeypatch.setattr(client, "_generate_once", bad_model)
    result = client.generate_structured(prompt="x", response_model=DiagnosisResponse, task="t")

    assert calls == [settings.gemini_primary_model, settings.gemini_fallback_model]
    assert result.degraded is True


# ===========================================================================
# Failover and the deterministic floor.
# ===========================================================================


def test_the_fallback_model_answers_when_the_primary_is_down(client, monkeypatch):
    def primary_down(model, prompt, response_model):
        if model == settings.gemini_primary_model:
            raise LLMUnavailableError("429 RESOURCE_EXHAUSTED")
        return valid_response()

    monkeypatch.setattr(client, "_generate_once", primary_down)
    result = client.generate_structured(prompt="x", response_model=DiagnosisResponse, task="t")

    assert result.ok is True
    assert result.model == settings.gemini_fallback_model
    assert result.degraded is True


def test_all_models_failing_reports_failure_without_raising(client, monkeypatch):
    """Never raises. Every caller has a deterministic path for this."""
    monkeypatch.setattr(
        client,
        "_generate_once",
        lambda m, p, r: (_ for _ in ()).throw(LLMUnavailableError("503")),
    )
    result = client.generate_structured(prompt="x", response_model=DiagnosisResponse, task="t")

    assert result.failed is True
    assert result.value is None
    assert result.attempts == (settings.gemini_primary_model, settings.gemini_fallback_model)


def test_an_empty_response_is_treated_as_a_failure(client, monkeypatch):
    monkeypatch.setattr(
        client,
        "_generate_once",
        lambda m, p, r: (_ for _ in ()).throw(LLMUnavailableError("empty response")),
    )
    assert (
        client.generate_structured(prompt="x", response_model=DiagnosisResponse, task="t").failed
        is True
    )


def test_no_api_key_fails_immediately_without_a_call():
    """Tests and half-configured deploys must not reach the network."""
    result = LLMClient(api_key="PLACEHOLDER").generate_structured(
        prompt="x", response_model=DiagnosisResponse, task="t"
    )
    assert result.failed is True
    assert result.error == "no_api_key"
    assert result.attempts == ()


# ===========================================================================
# Malformed output.
# ===========================================================================


def test_malformed_json_gets_one_repair_attempt(client, monkeypatch):
    """A schema violation is often one bad field, and re-asking is far cheaper than
    failing over. One attempt only — a second would be hoping."""
    from pydantic import ValidationError

    calls = []

    def sometimes_malformed(model, prompt, response_model):
        calls.append(prompt)
        if len(calls) == 1:
            raise ValidationError.from_exception_data("DiagnosisResponse", [])
        return valid_response()

    monkeypatch.setattr(client, "_generate_once", sometimes_malformed)
    result = client.generate_structured(
        prompt="original", response_model=DiagnosisResponse, task="t"
    )

    assert result.ok is True
    assert len(calls) == 2
    assert "did not match the required" in calls[1], "the repair prompt names the error"
    assert result.degraded is True, "a repaired answer is a degraded answer"


def test_repeated_schema_failure_falls_over(client, monkeypatch):
    from pydantic import ValidationError

    monkeypatch.setattr(
        client,
        "_generate_once",
        lambda m, p, r: (_ for _ in ()).throw(
            ValidationError.from_exception_data("DiagnosisResponse", [])
        ),
    )
    assert (
        client.generate_structured(prompt="x", response_model=DiagnosisResponse, task="t").failed
        is True
    )


# ===========================================================================
# The configured timeout is actually applied.
# ===========================================================================


def test_the_configured_timeout_reaches_the_sdk(monkeypatch):
    """The regression this guards: the setting existed and was never passed on."""
    captured = {}

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["timeout_ms"] = config.http_options.timeout
            raise LLMUnavailableError("stop here")

    class FakeSDK:
        models = FakeModels()

    monkeypatch.setattr(settings, "llm_timeout_seconds", 7.5, raising=False)
    c = LLMClient(api_key="real-looking-key")
    monkeypatch.setattr(c, "_sdk", lambda: FakeSDK())

    with pytest.raises(LLMUnavailableError):
        c._generate_once(settings.gemini_primary_model, "x", DiagnosisResponse)

    assert captured["timeout_ms"] == 7500
