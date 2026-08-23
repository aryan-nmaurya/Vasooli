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


def _is_configured() -> bool:
    key = settings.google_api_key
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
                    temperature=0.3,
                    max_output_tokens=1024,
                ),
            )
        except Exception as exc:  # transport, quota, bad model id, timeout
            raise LLMUnavailableError(f"{type(exc).__name__}: {exc}") from exc

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise LLMUnavailableError("empty response")
        return response_model.model_validate_json(text)

    def generate_structured[T: BaseModel](
        self,
        *,
        prompt: str,
        response_model: type[T],
        task: str,
        invoice_number: str | None = None,
    ) -> LLMResult[T]:
        """Try each model in turn. Never raises."""
        if not _is_configured():
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
                value = self._generate_once(model, prompt, response_model)
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
