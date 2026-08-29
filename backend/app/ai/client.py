"""LLM transport with a three-step failover chain. Doc §10.

    gemini-3.7-flash  →  gemini-3.6-flash  →  deterministic code

The third step is the one that matters. A quota wall mid-demo should be a footnote,
not a dead agent, and that is only true because the four reason categories are defined
as rules over customer history rather than as model judgments — the model contributes
explanation quality and phrasing, never core capability.

`generate_structured` never raises. Callers get an `LLMResult` that either holds a
validated object or reports failure, and every caller has a deterministic path for the
failure case.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("ai")


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    """Outcome of one structured generation attempt."""

    value: T | None
    model: str | None = None
    #: True when the primary model was not the one that answered.
    degraded: bool = False
    #: True when no model answered at all; the caller must use its own fallback.
    failed: bool = False
    error: str | None = None
    attempts: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.value is not None


class LLMUnavailableError(Exception):
    """Raised internally when a model cannot answer. Never escapes the client."""


#: Faults worth re-asking the same model about. Everything else is permanent for
#: that model, and the caller should fail over rather than burn quota on it.
#: Faults worth re-asking the SAME model about — a blip that clears in a second.
_RETRYABLE_MARKERS = (
    "500",
    "502",
    "503",
    "unavailable",
    "internal error",
    "connection",
    "temporarily",
)

#: Faults where the same model will keep saying no, but a DIFFERENT model may not.
#:
#: Quota is counted per model, so a 429 on the primary says nothing about the fallback
#: — failing over immediately is both faster and more likely to work than waiting. And
#: Gemini's own 429 carries `retryDelay: 12s`, so a 0.5s retry cannot succeed by
#: construction; it just spends half a second proving that.
#:
#: A 504 means the model is overloaded and each attempt burns the full timeout before
#: admitting it. Two retries at 20s each cost a minute to learn what the first attempt
#: already said.
_FAILOVER_IMMEDIATELY_MARKERS = (
    "429",
    "resource_exhausted",
    "rate limit",
    "quota",
    "504",
    "deadline",
    "timeout",
    "timed out",
)


def _is_retryable(message: str) -> bool:
    """Retry the same model, or move on?"""
    lowered = message.lower()
    if any(marker in lowered for marker in _FAILOVER_IMMEDIATELY_MARKERS):
        return False
    return any(marker in lowered for marker in _RETRYABLE_MARKERS)


def _key_is_configured(key: str | None) -> bool:
    """A real key, or a placeholder?

    Takes the key rather than reading settings, so a client constructed with an
    explicit key behaves consistently — the same instance-scoped check the Razorpay
    client uses.
    """
    return bool(key) and "PLACEHOLDER" not in key


class LLMClient:
    """Wraps Google AI Studio with model failover and schema validation."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.google_api_key
        self._client: Any = None

    def _sdk(self) -> Any:
        # Constructed lazily so importing this module never requires a key — the
        # deterministic paths must work on a machine that has none.
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _generate_once[T: BaseModel](self, model: str, prompt: str, response_model: type[T]) -> T:
        from google.genai import types

        try:
            response = self._sdk().models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_model,
                    # This client never gives the model tools. New google-genai
                    # releases enable automatic function calling by default and warn
                    # on direct generate_content calls unless it is disabled.
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    temperature=0.3,
                    max_output_tokens=1024,
                    # Enforced, not decorative. LLM_TIMEOUT_SECONDS existed in config
                    # and was never passed to the SDK. Without it a hung call blocks
                    # the recovery cycle, which runs synchronously — and a scheduled
                    # run that never returns looks exactly like one never scheduled.
                    http_options=types.HttpOptions(
                        timeout=int(settings.llm_timeout_seconds * 1000)
                    ),
                ),
            )
        except Exception as exc:  # transport, quota, bad model id, timeout
            raise LLMUnavailableError(f"{type(exc).__name__}: {exc}") from exc

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise LLMUnavailableError("empty response")
        return response_model.model_validate_json(text)

    def _retrying_generate[T: BaseModel](
        self, model: str, prompt: str, response_model: type[T]
    ) -> T:
        """One model, with bounded retries on transient faults.

        Short-lived connection faults and 5xx responses are worth re-asking the SAME
        model before giving up on it. Timeouts and quota errors fail over immediately:
        waiting through the same deadline again cannot rescue the current demo cycle.
        Permanent faults (bad model id, invalid request) also fail over without delay.

        LLM_MAX_RETRIES existed in config and was never read.
        """
        last: Exception | None = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                return self._generate_once(model, prompt, response_model)
            except LLMUnavailableError as exc:
                last = exc
                if not _is_retryable(str(exc)) or attempt == settings.llm_max_retries:
                    raise
                # Bounded, and capped at the timeout: a stuck model must not stall the
                # cycle for longer than one call was allowed to take.
                delay = min(0.5 * (2**attempt), settings.llm_timeout_seconds)
                log.info("llm.retrying", model=model, attempt=attempt + 1, delay=delay)
                time.sleep(delay)
        raise last  # unreachable, but keeps the type checker honest

    def generate_structured[T: BaseModel](
        self,
        *,
        prompt: str,
        response_model: type[T],
        task: str,
        invoice_number: str | None = None,
    ) -> LLMResult[T]:
        """Try each model in turn. Never raises."""
        if not _key_is_configured(self._api_key):
            return LLMResult(
                value=None,
                failed=True,
                degraded=True,
                error="no_api_key",
                attempts=(),
            )

        models = [settings.gemini_primary_model, settings.gemini_fallback_model]
        attempts: list[str] = []
        last_error = "unknown"

        for index, model in enumerate(models):
            attempts.append(model)
            try:
                value = self._retrying_generate(model, prompt, response_model)
                return LLMResult(
                    value=value,
                    model=model,
                    degraded=index > 0,
                    attempts=tuple(attempts),
                )
            except ValidationError as exc:
                # One repair attempt on the same model: schema violations are often a
                # single malformed field, and re-asking with the error attached fixes
                # it far more cheaply than failing over.
                last_error = f"schema: {exc.error_count()} error(s)"
                repair_prompt = (
                    f"{prompt}\n\nYour previous reply did not match the required "
                    f"schema:\n{exc}\n\nReply again with valid JSON only."
                )
                try:
                    value = self._generate_once(model, repair_prompt, response_model)
                    return LLMResult(
                        value=value,
                        model=model,
                        degraded=True,
                        attempts=tuple(attempts),
                    )
                except (ValidationError, LLMUnavailableError, json.JSONDecodeError) as repair_exc:
                    last_error = f"repair failed: {repair_exc}"
            except LLMUnavailableError as exc:
                last_error = str(exc)

            log.warning(
                "llm.failover",
                task=task,
                model=model,
                invoice_number=invoice_number,
                error=last_error,
            )

        log.warning("llm.all_models_failed", task=task, invoice_number=invoice_number)
        return LLMResult(
            value=None,
            failed=True,
            degraded=True,
            error=last_error,
            attempts=tuple(attempts),
        )


_default_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
