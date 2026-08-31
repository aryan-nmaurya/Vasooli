"""Immutable, merchant-editable reminder cadence versions."""

import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from app.models import ReminderPolicyVersion

PLATFORM_MIN_COOLDOWN_DAYS = 3
DEFAULT_POLICY = {"tier_offsets": [3, 10, 21], "cooldown_days": 7, "max_attempts": 3}
PRESETS = {
    "default": DEFAULT_POLICY,
    "3_7_14": {"tier_offsets": [3, 7, 14], "cooldown_days": 4, "max_attempts": 3},
}


def validate_policy(
    *, tier_offsets: list[int], cooldown_days: int, max_attempts: int, timezone: str
) -> None:
    if not tier_offsets or any(offset < 1 for offset in tier_offsets):
        raise ValueError("Tier offsets must be positive days")
    if tier_offsets != sorted(set(tier_offsets)):
        raise ValueError("Tier offsets must be strictly increasing")
    if cooldown_days < PLATFORM_MIN_COOLDOWN_DAYS:
        raise ValueError(f"Cooldown must be at least {PLATFORM_MIN_COOLDOWN_DAYS} days")
    for previous, current in zip(tier_offsets, tier_offsets[1:], strict=False):
        gap = current - previous
        if gap < cooldown_days:
            raise ValueError(
                f"Day {current} is only {gap} days after day {previous}, "
                f"but your cooldown is {cooldown_days} days"
            )
    if max_attempts < 1 or max_attempts > 3:
        raise ValueError("Max attempts must be between 1 and 3")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Unknown timezone") from exc


def create_policy(
    session: Session,
    merchant_id: uuid.UUID,
    *,
    tier_offsets: list[int],
    cooldown_days: int,
    max_attempts: int,
    timezone: str,
    channel: str = "email",
    created_by_user_id: uuid.UUID | None = None,
) -> ReminderPolicyVersion:
    validate_policy(
        tier_offsets=tier_offsets,
        cooldown_days=cooldown_days,
        max_attempts=max_attempts,
        timezone=timezone,
    )
    current = session.exec(
        select(ReminderPolicyVersion.version)
        .where(ReminderPolicyVersion.merchant_id == merchant_id)
        .order_by(ReminderPolicyVersion.version.desc())
    ).first()
    row = ReminderPolicyVersion(
        merchant_id=merchant_id,
        version=(current or 0) + 1,
        tier_offsets=tier_offsets,
        cooldown_days=cooldown_days,
        max_attempts=max_attempts,
        timezone=timezone,
        channel=channel,
        created_by_user_id=created_by_user_id,
    )
    for old in session.exec(
        select(ReminderPolicyVersion).where(
            ReminderPolicyVersion.merchant_id == merchant_id,
            ReminderPolicyVersion.is_active.is_(True),  # type: ignore[union-attr]
        )
    ).all():
        old.is_active = False
        session.add(old)
    session.add(row)
    session.flush()
    return row
