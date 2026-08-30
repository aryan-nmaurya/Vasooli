"""Suppression, sending-domain and per-merchant outbound quota checks."""

import uuid
from datetime import date

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.config import settings
from app.models import Customer, MerchantUsageBucket, SendingDomain, SuppressionEntry


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
    if settings.global_send_kill_switch:
        raise OutboundBlockedError("Outbound email is paused by the platform kill switch")
    today = bucket_date or utcnow().date()
    global_sent = session.exec(
        select(func.coalesce(func.sum(MerchantUsageBucket.sent_count), 0)).where(
            MerchantUsageBucket.bucket_date == today
        )
    ).one()
    if int(global_sent or 0) >= settings.global_daily_send_quota:
        raise OutboundBlockedError("Global daily outbound email quota exceeded")
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


def sending_domain_is_verified(session: Session, merchant_id: uuid.UUID) -> bool:
    """Has this merchant proven they own the domain their mail claims to come from?

    Recorded but unenforced until now: `SendingDomain` rows and the verification API
    existed, and nothing on the send path consulted them. A live merchant could send
    from a domain with no SPF or DKIM, which is the textbook spam profile — the mail
    is filtered, the merchant concludes the product does not work, and the sending
    reputation of every other merchant on the platform degrades with it.

    Collections mail is exactly the category providers scrutinise hardest, so this is
    a gate rather than a warning. Demo sending is unaffected: it is redirected to an
    operator inbox and never reaches a customer, so there is no reputation to protect
    and no domain to prove.
    """
    return (
        session.exec(
            select(SendingDomain).where(
                SendingDomain.merchant_id == merchant_id,
                SendingDomain.status == "verified",
            )
        ).first()
        is not None
    )


def assert_can_send(session: Session, merchant_id: uuid.UUID, *, is_demo: bool) -> None:
    """Every precondition that is about the merchant rather than the recipient.

    Raises `OutboundBlockedError`, which the delivery path already records as a
    non-retryable failure — retrying an unverified domain would just fail identically
    while burning quota.
    """
    if is_demo:
        return
    if not sending_domain_is_verified(session, merchant_id):
        raise OutboundBlockedError(
            "No verified sending domain. Verify one under Settings before sending live "
            "reminders — unverified mail is filtered as spam and harms deliverability "
            "for every merchant on this platform."
        )
