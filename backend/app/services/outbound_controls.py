"""Suppression, sending-domain and per-merchant outbound quota checks."""

import uuid
from datetime import date

from sqlalchemy import and_, func, or_, text
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
    """Is this recipient on the merchant's do-not-contact list?

    Answered in SQL rather than in Python. The earlier version loaded every active
    suppression row for the merchant and filtered in memory — on every send. A
    suppression list only grows: every hard bounce, complaint, unsubscribe and legal
    hold adds a row and nothing removes them. A merchant a year in has thousands, and
    a cycle over five hundred invoices would load all of them five hundred times, in
    the delivery loop, for a question whose answer is one indexed row.

    Two details that would quietly break the match if changed:

    * The identity comparisons are guarded so a NULL never meets a NULL. An
      address-only row has no `customer_id`, and `customer_id = NULL` matching a
      caller who passed no customer is how one bounce silently muted an entire
      merchant's ledger. `IS NOT NULL` on both sides keeps that impossible.
    * Addresses are compared case-folded on both sides. `STOP@x.com` and `stop@x.com`
      are one mailbox, and a case-sensitive match would let a differently-cased copy
      of a bounced address straight through.
    """
    if customer is None and not email:
        return False

    identity = []
    if customer is not None:
        identity.append(
            and_(
                SuppressionEntry.customer_id.is_not(None),  # type: ignore[union-attr]
                SuppressionEntry.customer_id == customer.id,
            )
        )
    if email:
        identity.append(
            and_(
                SuppressionEntry.email.is_not(None),  # type: ignore[union-attr]
                func.lower(SuppressionEntry.email) == email.casefold(),
            )
        )

    now = utcnow()
    found = session.exec(
        select(SuppressionEntry.id)
        .where(
            SuppressionEntry.merchant_id == merchant_id,
            SuppressionEntry.active.is_(True),  # type: ignore[union-attr]
            or_(*identity),
            or_(
                SuppressionEntry.expires_at.is_(None),  # type: ignore[union-attr]
                SuppressionEntry.expires_at > now,
            ),
        )
        .limit(1)
    ).first()
    return found is not None


def claim_send_slot(
    session: Session,
    merchant_id: uuid.UUID,
    *,
    quota: int = 100,
    bucket_date: date | None = None,
) -> MerchantUsageBucket:
    """Consume one slot from today's allowance, or refuse.

    The check and the increment are a single statement on purpose. The earlier form
    read `sent_count`, compared it in Python, then wrote back — a lost update between
    any two concurrent senders, each seeing 99, each deciding 99 < 100, each writing
    100. The daily cap exists to protect a sending domain's reputation, so quietly
    exceeding it is exactly the failure it was meant to prevent.

    Concurrency here is not hypothetical: the recovery cycle and the retry sweep hold
    *different* advisory locks, so they can overlap, and the plan's direction is more
    workers rather than fewer.

    `ON CONFLICT ... WHERE` makes Postgres do the comparison while holding the row
    lock. No row comes back when the cap is reached, which is the refusal.
    """
    if settings.global_send_kill_switch:
        raise OutboundBlockedError("Outbound email is paused by the platform kill switch")

    today = bucket_date or utcnow().date()

    # Platform-wide ceiling, checked before the per-merchant one so a single busy
    # tenant cannot spend everyone else's headroom. Read-only, and approximate under
    # concurrency by design — it is a backstop, not the enforcement point.
    global_sent = session.exec(
        select(func.coalesce(func.sum(MerchantUsageBucket.sent_count), 0)).where(
            MerchantUsageBucket.bucket_date == today
        )
    ).one()
    if int(global_sent or 0) >= settings.global_daily_send_quota:
        raise OutboundBlockedError("Global daily outbound email quota exceeded")

    claimed = session.exec(
        text(
            """
            INSERT INTO merchant_usage_buckets
                (id, merchant_id, bucket_date, sent_count, failed_count, quota,
                 created_at, updated_at)
            VALUES (gen_random_uuid(), :merchant_id, :bucket_date, 1, 0, :quota,
                    now(), now())
            ON CONFLICT (merchant_id, bucket_date) DO UPDATE
                SET sent_count = merchant_usage_buckets.sent_count + 1,
                    updated_at = now()
                WHERE merchant_usage_buckets.sent_count < merchant_usage_buckets.quota
            RETURNING id
            """
        ).bindparams(merchant_id=merchant_id, bucket_date=today, quota=quota)
    ).first()

    if claimed is None:
        raise OutboundBlockedError("Daily outbound email quota exceeded")

    bucket = session.exec(
        select(MerchantUsageBucket).where(
            MerchantUsageBucket.merchant_id == merchant_id,
            MerchantUsageBucket.bucket_date == today,
        )
    ).one()
    # The row was changed by raw SQL, so a previously-loaded copy in this session is
    # stale; refresh before anything reads the count back.
    session.refresh(bucket)
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
    if settings.allow_platform_sender_for_live:
        return
    if not sending_domain_is_verified(session, merchant_id):
        raise OutboundBlockedError(
            "No verified sending domain. Verify one under Settings before sending live "
            "reminders — unverified mail is filtered as spam and harms deliverability "
            "for every merchant on this platform."
        )
