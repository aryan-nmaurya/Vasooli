"""Reply history in diagnosis. P1 correctness.

The bug: `has_reply` was hardcoded False in the recovery cycle. Doc §3 defines
"unresponsive" as no reply after the Tier 2 reminder, so every customer who wrote back
was still eventually classified unresponsive — the category reserved for people who
ignore you. That changes the tone of the next reminder and can hand a cooperative
customer to a human as a defaulter.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.core.constants import InvoiceStatus, ReasonCategory
from app.models import Invoice, Promise
from app.services.recovery import run_recovery_cycle
from app.services.replies import handle_reply


@pytest.fixture
def chasing(session, merchant, customer) -> Invoice:
    """A clean payer, two reminders in, at the point where "unresponsive" applies."""
    due = datetime.now(UTC) - timedelta(days=12)
    invoice = Invoice(
        merchant_id=merchant.id,
        customer_id=customer.id,
        invoice_number="INV-RH1",
        amount_paise=2_500_000,
        issued_at=due - timedelta(days=30),
        due_at=due,
        status=InvoiceStatus.CHASING,
        current_tier=2,
        reminders_sent=2,
    )
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice


def cycle(session, **kw):
    kw.setdefault("use_llm", False)
    return run_recovery_cycle(session, **kw)


# ===========================================================================
# Persistence.
# ===========================================================================


def test_a_reply_is_recorded_on_the_invoice(session, chasing):
    handle_reply(session, chasing, "I cannot pay until Friday.", use_llm=False)
    session.refresh(chasing)

    assert chasing.reply_count == 1
    assert chasing.has_replied is True
    assert chasing.last_reply_at is not None
    assert "Friday" in chasing.last_reply_excerpt


def test_every_reply_is_counted(session, chasing):
    for text in ["Thanks, noted.", "Still checking.", "Next week maybe."]:
        handle_reply(session, chasing, text, use_llm=False)
    session.refresh(chasing)

    assert chasing.reply_count == 3
    assert "Next week" in chasing.last_reply_excerpt, "the latest reply is kept"


def test_a_vague_reply_still_counts_as_a_reply(session, chasing):
    """ "I'll look into it" creates no promise — but the customer is not ignoring us."""
    handle_reply(session, chasing, "I'll look into it.", use_llm=False)
    session.refresh(chasing)

    assert chasing.has_replied is True
    assert session.exec(select(Promise)).all() == []


# ===========================================================================
# The bug itself.
# ===========================================================================


def test_a_customer_who_replied_is_not_called_unresponsive(session, chasing):
    """The headline case. "I cannot pay until Friday" is not silence."""
    handle_reply(session, chasing, "I cannot pay until Friday.", use_llm=False)
    session.refresh(chasing)
    chasing.status = InvoiceStatus.CHASING  # a promise may have paused it
    session.add(chasing)
    session.commit()

    cycle(session)
    session.refresh(chasing)
    assert chasing.reason_category != ReasonCategory.UNRESPONSIVE


def test_a_customer_who_never_replied_is_still_unresponsive(session, chasing):
    """The rule must keep working — this is not a blanket exemption."""
    assert chasing.has_replied is False

    cycle(session)
    session.refresh(chasing)
    assert chasing.reason_category == ReasonCategory.UNRESPONSIVE


def test_reply_history_survives_across_cycles(session, chasing):
    """Diagnosis re-runs as tiers advance, so this has to be persisted, not in-memory."""
    handle_reply(session, chasing, "Payment is being processed.", use_llm=False)
    session.refresh(chasing)
    chasing.status = InvoiceStatus.CHASING
    session.add(chasing)
    session.commit()

    for _ in range(3):
        cycle(session)
        session.refresh(chasing)
        assert chasing.reply_count >= 1
        assert chasing.reason_category != ReasonCategory.UNRESPONSIVE


# ===========================================================================
# Dispute state is remembered too.
# ===========================================================================


def test_a_complaint_keeps_its_classification_on_later_cycles(session, chasing):
    handle_reply(session, chasing, "We were billed for 12 units but received 9.", use_llm=False)
    session.refresh(chasing)
    assert chasing.reason_category == ReasonCategory.DISPUTE_LIKELY

    cycle(session)
    session.refresh(chasing)
    assert chasing.reason_category == ReasonCategory.DISPUTE_LIKELY
    assert chasing.status == InvoiceStatus.HUMAN_REVIEW


def test_a_later_promise_does_not_erase_a_dispute(session, chasing):
    """Contradictory replies: once disputed, an automated chase is still wrong.

    The invoice is already with a human, and that is who should judge whether the
    dispute is resolved — not a keyword match on the next message.
    """
    handle_reply(session, chasing, "This doesn't match the PO we signed.", use_llm=False)
    handle_reply(session, chasing, "We will pay on 2026-09-20.", use_llm=False)

    session.refresh(chasing)
    assert chasing.status == InvoiceStatus.HUMAN_REVIEW
    assert chasing.reply_count == 2

    report = cycle(session)
    assert report.sent == 0, "a disputed invoice is never chased automatically"
