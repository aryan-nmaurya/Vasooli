"""Suppression, sending-domain and per-merchant outbound quota checks."""

import uuid
from datetime import date

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.models import Customer, MerchantUsageBucket, SuppressionEntry


class OutboundBlockedError(ValueError):
    """The message must not be sent."""


def is_suppressed(
    session: Session,
    merchant_id: uuid.UUID,
    *,
    customer: Customer | None = None,
    email: str | None = None,
) -> bool:
    if customer is None and not email:
        return False

    rows = session.exec(
        select(SuppressionEntry).where(
            SuppressionEntry.merchant_id == merchant_id,
            SuppressionEntry.active.is_(True),  # type: ignore[union-attr]
        )
    ).all()
    now = utcnow()
    wanted_email = email.casefold() if email else None

    def matches(row: SuppressionEntry) -> bool:
        """Both sides of a comparison must actually be present.

        The earlier form compared `row.customer_id == (customer.id if customer else
        None)`. Called without a customer that is `None == None`, which is True — so a
        single address-only suppression row matched every address on the merchant, and
        one hard bounce silently blocked every reminder they had left to send. The
        failure was invisible: each send was recorded as "customer is suppressed" and
        never retried.
        """
        if customer is not None and row.customer_id == customer.id:
            return True
        return (
            wanted_email is not None
            and row.email is not None
            and row.email.casefold() == wanted_email
        )

    return any(matches(row) and (row.expires_at is None or row.expires_at > now) for row in rows)


def claim_send_slot(
    session: Session,
    merchant_id: uuid.UUID,
    *,
    quota: int = 100,
    bucket_date: date | None = None,
) -> MerchantUsageBucket:
    today = bucket_date or utcnow().date()
    bucket = session.exec(
        select(MerchantUsageBucket).where(
            MerchantUsageBucket.merchant_id == merchant_id,
            MerchantUsageBucket.bucket_date == today,
        )
    ).first()
    if bucket is None:
        bucket = MerchantUsageBucket(merchant_id=merchant_id, bucket_date=today, quota=quota)
    if bucket.sent_count >= bucket.quota:
        raise OutboundBlockedError("Daily outbound email quota exceeded")
    bucket.sent_count += 1
    session.add(bucket)
    session.flush()
    return bucket
