"""Suppression and per-merchant outbound quota.

These are the controls that stand between a real merchant and a complaint. A
suppressed address is one that bounced, complained, unsubscribed, or is under legal
hold: sending to it again costs deliverability for every other merchant on the same
domain, and in the complaint case it is the customer explicitly saying stop.

Both are checked in `messaging.deliver_reminder` before the provider call, and both
are deliberately skipped for the frozen demo — see the `is_demo` guards there.
"""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.core.clock import utcnow
from app.models import MerchantUsageBucket, SuppressionEntry
from app.services.outbound_controls import OutboundBlockedError, claim_send_slot, is_suppressed


def _suppress(session, merchant, *, email=None, customer=None, reason="hard_bounce", **over):
    entry = SuppressionEntry(
        merchant_id=merchant.id,
        email=email.casefold() if email else None,
        customer_id=customer.id if customer else None,
        reason=reason,
        **over,
    )
    session.add(entry)
    session.commit()
    return entry


# ===========================================================================
# Suppression
# ===========================================================================


@pytest.mark.parametrize(
    "reason", ["unsubscribe", "hard_bounce", "abuse_complaint", "legal_hold", "merchant_block"]
)
def test_every_suppression_reason_blocks_the_address(session, merchant, reason):
    """The plan names five reasons. None of them is advisory."""
    _suppress(session, merchant, email="stop@buyer.example.com", reason=reason)
    assert is_suppressed(session, merchant.id, email="stop@buyer.example.com") is True


def test_an_unsuppressed_address_is_not_blocked(session, merchant):
    _suppress(session, merchant, email="stop@buyer.example.com")
    assert is_suppressed(session, merchant.id, email="other@buyer.example.com") is False


def test_suppression_ignores_address_case(session, merchant):
    """`STOP@…` and `stop@…` are one mailbox. Case-sensitive matching would let a
    differently-cased copy of a bounced address straight through."""
    _suppress(session, merchant, email="stop@buyer.example.com")
    assert is_suppressed(session, merchant.id, email="STOP@Buyer.Example.COM") is True


def test_suppression_is_scoped_to_one_merchant(session, merchant, customer):
    """One merchant's block must not silence another merchant's mail. The same buyer
    commonly owes several suppliers, and unsubscribing from one is not a global opt-out."""
    from app.models import Merchant

    other = Merchant(name="Other Traders", contact_email="ops@other.example.test")
    session.add(other)
    session.commit()
    session.refresh(other)

    _suppress(session, merchant, email="stop@buyer.example.com")

    assert is_suppressed(session, merchant.id, email="stop@buyer.example.com") is True
    assert is_suppressed(session, other.id, email="stop@buyer.example.com") is False


def test_an_inactive_suppression_does_not_block(session, merchant):
    _suppress(session, merchant, email="stop@buyer.example.com", active=False)
    assert is_suppressed(session, merchant.id, email="stop@buyer.example.com") is False


def test_an_expired_suppression_does_not_block(session, merchant):
    """A temporary hold has to actually lift, or nothing is ever chased again."""
    _suppress(
        session,
        merchant,
        email="stop@buyer.example.com",
        expires_at=utcnow() - timedelta(days=1),
    )
    assert is_suppressed(session, merchant.id, email="stop@buyer.example.com") is False


def test_a_future_expiry_still_blocks(session, merchant):
    _suppress(
        session,
        merchant,
        email="stop@buyer.example.com",
        expires_at=utcnow() + timedelta(days=1),
    )
    assert is_suppressed(session, merchant.id, email="stop@buyer.example.com") is True


def test_a_customer_can_be_suppressed_without_an_address(session, merchant, customer):
    """Suppressing the record, not the string — so it survives a corrected email."""
    _suppress(session, merchant, customer=customer, reason="legal_hold")
    assert is_suppressed(session, merchant.id, customer=customer) is True


def test_no_identifier_means_no_suppression(session, merchant):
    """Called with neither an address nor a customer, this must not block everything."""
    _suppress(session, merchant, email="stop@buyer.example.com")
    assert is_suppressed(session, merchant.id) is False


# ===========================================================================
# Quota
# ===========================================================================


def test_the_first_send_of_the_day_opens_a_bucket(session, merchant):
    bucket = claim_send_slot(session, merchant.id, quota=3)
    assert bucket.sent_count == 1
    assert bucket.bucket_date == utcnow().date()


def test_each_send_consumes_one_slot(session, merchant):
    for expected in (1, 2, 3):
        assert claim_send_slot(session, merchant.id, quota=3).sent_count == expected


def test_exceeding_the_quota_is_refused(session, merchant):
    for _ in range(3):
        claim_send_slot(session, merchant.id, quota=3)
    with pytest.raises(OutboundBlockedError, match="quota"):
        claim_send_slot(session, merchant.id, quota=3)


def test_the_quota_is_per_merchant(session, merchant):
    """A busy merchant must not exhaust a quiet one's allowance."""
    from app.models import Merchant

    other = Merchant(name="Other Traders", contact_email="ops@other.example.test")
    session.add(other)
    session.commit()
    session.refresh(other)

    for _ in range(3):
        claim_send_slot(session, merchant.id, quota=3)

    assert claim_send_slot(session, other.id, quota=3).sent_count == 1


def test_the_quota_resets_on_a_new_day(session, merchant):
    """Buckets are per date, so yesterday's exhaustion must not carry over."""
    yesterday = utcnow().date() - timedelta(days=1)
    for _ in range(3):
        claim_send_slot(session, merchant.id, quota=3, bucket_date=yesterday)

    assert claim_send_slot(session, merchant.id, quota=3).sent_count == 1

    buckets = session.exec(
        select(MerchantUsageBucket).where(MerchantUsageBucket.merchant_id == merchant.id)
    ).all()
    assert len(buckets) == 2
