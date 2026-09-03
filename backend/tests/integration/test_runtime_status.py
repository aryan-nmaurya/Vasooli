"""What the runtime banner reports, and why it must be measured rather than configured.

The banner is the one thing the reviewer guide points a judge at and calls honest, so
its two derived fields are pinned here.

`ai` in particular had a subtle failure that no test caught: it read only
`Reminder.generated_by`, and a reminder row exists only when one is actually SENT. A
ledger with nothing currently due therefore produces no new evidence at all, so the
banner stayed pinned to whatever the last send happened to be — production read
`degraded` for a day after the models had recovered, and no number of recovery cycles
could clear it, because none of them had anything to send.
"""

from app.core.constants import TONE_FOR_TIER
from app.models import AuditAction, AuditActor, AuditLog, Reminder
from app.services.runtime_status import ai_health


def _diagnosed(session, invoice, source: str) -> None:
    """One `diagnosed` audit row — written on EVERY diagnosis, sent or not."""
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            actor=AuditActor.AI,
            action=AuditAction.DIAGNOSED,
            detail={"category": "oversight", "source": source, "confidence": 1.0},
        )
    )
    session.commit()


def _reminder(session, invoice, generated_by: str, tier: int = 1) -> None:
    # `tier` varies because (invoice_id, tier) is unique — one reminder per tier.
    session.add(
        Reminder(
            invoice_id=invoice.id,
            tier=tier,
            tone=TONE_FOR_TIER[tier],
            subject="s",
            body="b",
            generated_by=generated_by,
        )
    )
    session.commit()


def test_no_evidence_is_not_reported_as_failure(session):
    """A fresh deployment has answered nothing. That is not the same as broken."""
    assert ai_health(session) == "enabled"


def test_a_real_model_answering_diagnosis_counts_as_healthy(session, invoice):
    """The regression: diagnosis proves the models are up even with nothing to send."""
    _diagnosed(session, invoice, "gemini-3.5-flash")
    assert ai_health(session) == "enabled"


def test_only_fallbacks_is_degraded(session, invoice):
    _diagnosed(session, invoice, "rule_based")
    _reminder(session, invoice, "template_fallback")
    assert ai_health(session) == "degraded"


def test_one_recent_success_clears_a_run_of_fallbacks(session, invoice):
    """A model answering now outranks older fallbacks; the banner tracks the present."""
    _reminder(session, invoice, "template_fallback")
    _diagnosed(session, invoice, "rule_based")
    _diagnosed(session, invoice, "gemini-3.6-flash")
    assert ai_health(session) == "enabled"


def test_stale_sends_no_longer_pin_the_banner(session, invoice):
    """Exactly the production case.

    Old reminders all fell back, nothing is due to send, but diagnosis is answering.
    Before, this read `degraded` forever.
    """
    for tier in (1, 2, 3):
        _reminder(session, invoice, "template_fallback", tier=tier)
    _diagnosed(session, invoice, "gemini-3.5-flash")
    assert ai_health(session) == "enabled"


def test_no_api_key_is_disabled_not_degraded(session, monkeypatch):
    """'Disabled' is a configuration statement; 'degraded' is a measurement."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "google_api_key", "")
    assert ai_health(session) == "disabled"
