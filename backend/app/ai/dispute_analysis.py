"""Read a customer's objection in structured form. Customer Conversation Safety.

The promise extractor already answers "is this a complaint?" as a bare boolean. That
is enough to stop chasing, and not nearly enough for a person to act. This module asks
the follow-up a human would ask next — what exactly are they objecting to, and which
of their claims can I go and check against a delivery note?

It runs on the SAME client, the same model list, the same failover chain, the same
timeout and the same schema validation as every other AI task here. There is no second
client and no second agent; there is one more structured question.

**This module understands. It does not decide.** It returns a description of a
message. Whether recovery pauses is decided by app.policy.disputes from this
description, and the pause is written by app.services.disputes. As with every module
under app.ai, that separation is structural rather than promised: nothing reachable
from here can write invoice state, send anything, or touch a payment.
"""

from dataclasses import dataclass, field

from app.ai.client import LLMClient, get_llm_client
from app.ai.prompts.analyse_dispute import ANALYSE_DISPUTE_PROMPT
from app.ai.schemas import DisputeSignal
from app.core.logging import get_logger
from app.core.money import paise_to_rupees

log = get_logger("ai.dispute")

#: Longest excerpt kept from the customer's own words. Matches the promise excerpt
#: limit so both paths truncate identically.
MAX_EXCERPT = 300

#: What the deterministic path reports when it has matched a complaint marker but has
#: no idea what the complaint is about. Deliberately middling: it is a real signal
#: (a marker matched) and a poor one (a regex matched a word).
RULE_BASED_CONFIDENCE = 0.5

#: Facts are for a person to tick off one at a time. More than this and the card
#: stops being scannable, which was the whole point of extracting them.
MAX_FACTS = 6


@dataclass(frozen=True)
class DisputeAnalysis:
    """What the customer appears to be objecting to."""

    is_dispute: bool
    reason: str = ""
    summary: str = ""
    confidence: float = 0.0
    facts: tuple[str, ...] = field(default_factory=tuple)
    #: Model that answered, or "rule_based" when none did.
    source: str = "rule_based"
    #: True when the primary model was not the one that produced this.
    degraded: bool = False
    #: True when no model answered at all and the regex path produced this.
    used_fallback: bool = True
    #: Populated when a model was attempted and failed, for the audit record.
    error: str | None = None
    models_attempted: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# Deterministic fallback.
# --------------------------------------------------------------------------


def rule_based_analysis(reply_body: str) -> DisputeAnalysis:
    """What to say about a dispute when no model is available.

    Reuses the promise extractor's complaint markers rather than keeping a second
    list: two lists of the same words drift apart, and then a reply is a complaint on
    one code path and not on the other.

    Deliberately says very little. It reports THAT the customer objected and quotes
    them; it does not invent a reason or manufacture facts. An empty facts list is an
    honest answer, and the merchant still has the message itself.
    """
    # Imported here rather than at module scope: the markers are an implementation
    # detail of the promise extractor, and a top-level import would make this module
    # look like it depends on promise extraction when it only borrows a word list.
    from app.ai.promise_extraction import _COMPLAINT_MARKERS, _expand

    text = _expand(reply_body)
    matched = [m for m in _COMPLAINT_MARKERS if m in text]

    if not matched:
        return DisputeAnalysis(is_dispute=False, source="rule_based")

    return DisputeAnalysis(
        is_dispute=True,
        reason="Customer raised an objection in their reply",
        summary=(
            "No model was available to read this message. The customer used language "
            "that signals a complaint, so recovery was paused and the message is "
            "shown unedited below."
        ),
        confidence=RULE_BASED_CONFIDENCE,
        facts=(),
        source="rule_based",
        degraded=True,
        used_fallback=True,
    )


# --------------------------------------------------------------------------
# Model path.
# --------------------------------------------------------------------------


def _clean_facts(raw: list[str]) -> tuple[str, ...]:
    """Keep the checkable claims, drop the noise.

    A model occasionally returns an empty string, a duplicate, or a paragraph where a
    claim was asked for. None of those are useful on a card a person is meant to tick
    off, and all three are cheaper to drop here than to render badly.
    """
    seen: list[str] = []
    for item in raw:
        text = str(item).strip()
        if not text or len(text) > 200:
            continue
        if text.lower() in {s.lower() for s in seen}:
            continue
        seen.append(text)
        if len(seen) == MAX_FACTS:
            break
    return tuple(seen)


def analyse_dispute(
    reply_body: str,
    *,
    invoice_number: str,
    outstanding_paise: int,
    client: LLMClient | None = None,
    use_llm: bool = True,
) -> DisputeAnalysis:
    """Describe the objection in a customer's reply.

    Never raises and never returns None. Every failure mode — no API key, timeout,
    both models down, malformed JSON, schema violation — lands on the deterministic
    path, because a dispute that goes unrecorded because the AI was down is exactly
    the customer who then gets chased.
    """
    if not reply_body.strip():
        return DisputeAnalysis(is_dispute=False, source="rule_based")

    if not use_llm:
        return rule_based_analysis(reply_body)

    client = client or get_llm_client()
    prompt = ANALYSE_DISPUTE_PROMPT.format(
        invoice_number=invoice_number,
        outstanding_inr=paise_to_rupees(outstanding_paise),
        reply_body=reply_body,
    )

    result = client.generate_structured(
        prompt=prompt,
        response_model=DisputeSignal,
        task="analyse_dispute",
        invoice_number=invoice_number,
    )

    if not result.ok or result.value is None:
        log.info(
            "dispute.fallback",
            invoice_number=invoice_number,
            error=result.error,
            attempts=list(result.attempts),
        )
        fallback = rule_based_analysis(reply_body)
        return DisputeAnalysis(
            **{
                **fallback.__dict__,
                "error": result.error,
                "models_attempted": result.attempts,
            }
        )

    value: DisputeSignal = result.value

    # A model that says "not a dispute" is answering the question it was asked, and
    # the answer is accepted. The caller only reaches this module when something else
    # already suspected a complaint, so a `false` here is the model disagreeing with
    # the extractor — which is information, not a failure.
    return DisputeAnalysis(
        is_dispute=value.is_dispute,
        reason=value.reason.strip()[:120] or "Customer raised an objection",
        summary=value.summary.strip()[:400],
        confidence=value.confidence,
        facts=_clean_facts(value.facts),
        source=result.model or "rule_based",
        degraded=result.degraded,
        used_fallback=False,
        models_attempted=result.attempts,
    )
