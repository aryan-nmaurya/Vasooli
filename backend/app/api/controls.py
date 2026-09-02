"""Merchant recovery policy, suppression and sending-domain controls."""

import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.clock import utcnow
from app.core.db import SessionDep
from app.core.middleware import client_ip
from app.models import ReminderPolicyVersion, SendingDomain, SuppressionEntry
from app.services.auth import audit
from app.services.authorization import LiveContext, require_live_permission
from app.services.billing import (
    BillingEntitlementError,
    assert_feature_entitled,
    assert_write_allowed,
)
from app.services.plans import Feature
from app.services.policy_versions import PRESETS, create_policy
from app.services.sending_domains import (
    provider_domain_status,
    provision_provider_domain,
    verify_domain_dns,
)

router = APIRouter(prefix="/api/live/controls", tags=["live-controls"])


class PolicyRequest(BaseModel):
    preset: str | None = Field(default=None, pattern=r"^(default|3_7_14)$")
    tier_offsets: list[int] | None = None
    cooldown_days: int | None = Field(default=None, ge=1, le=30)
    max_attempts: int | None = Field(default=None, ge=1, le=3)
    timezone: str = "Asia/Kolkata"
    channel: str = Field(default="email", pattern=r"^email$")


class SuppressionRequest(BaseModel):
    customer_id: uuid.UUID | None = None
    email: str | None = Field(default=None, min_length=3, max_length=320)
    reason: str = Field(pattern=r"^(unsubscribe|hard_bounce|complaint|legal_hold|merchant_block)$")
    expires_at: datetime | None = None


@router.get("/policy")
def get_policy(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("reminder.read"))],
) -> dict[str, Any] | None:
    row = session.exec(
        select(ReminderPolicyVersion)
        .where(ReminderPolicyVersion.merchant_id == context.merchant.id)
        .order_by(ReminderPolicyVersion.version.desc())
    ).first()
    if row is None:
        return None
    return _policy_dict(row)


@router.put("/policy", status_code=status.HTTP_201_CREATED)
def put_policy(
    payload: PolicyRequest,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("reminder.configure"))],
) -> dict[str, Any]:
    values = PRESETS[payload.preset] if payload.preset else {}

    # Automation is paused while billing is unpaid, and the cadence is automation.
    try:
        assert_write_allowed(session, context.merchant.id)
    except BillingEntitlementError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc

    # "Custom recovery policies" is a Growth capability. Every plan may still choose a
    # published preset — the gate is on hand-tuned values, not on having a policy.
    if any(
        v is not None for v in (payload.tier_offsets, payload.cooldown_days, payload.max_attempts)
    ):
        try:
            assert_feature_entitled(session, context.merchant.id, Feature.CUSTOM_POLICIES)
        except BillingEntitlementError as exc:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc)) from exc

    try:
        row = create_policy(
            session,
            context.merchant.id,
            tier_offsets=payload.tier_offsets or values.get("tier_offsets", [3, 10, 21]),
            cooldown_days=payload.cooldown_days or values.get("cooldown_days", 7),
            max_attempts=payload.max_attempts or values.get("max_attempts", 3),
            timezone=payload.timezone,
            channel=payload.channel,
            created_by_user_id=context.user.id,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    audit(
        session,
        action="reminder_policy.created",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="reminder_policy_version",
        object_id=row.id,
        ip_address=client_ip(request),
        detail={"version": row.version, "tier_offsets": row.tier_offsets},
    )
    session.commit()
    return _policy_dict(row)


@router.post("/suppressions", status_code=status.HTTP_201_CREATED)
def add_suppression(
    payload: SuppressionRequest,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("reminder.pause"))],
) -> dict[str, str]:
    if payload.customer_id is None and payload.email is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "customer_id or email is required"
        )
    row = SuppressionEntry(
        merchant_id=context.merchant.id,
        customer_id=payload.customer_id,
        email=payload.email.casefold() if payload.email else None,
        reason=payload.reason,
        expires_at=payload.expires_at,
    )
    session.add(row)
    session.commit()
    return {"id": str(row.id), "status": "active", "reason": row.reason}


@router.get("/sending-domains")
def sending_domains(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("merchant.read"))],
) -> list[dict[str, Any]]:
    rows = session.exec(
        select(SendingDomain).where(SendingDomain.merchant_id == context.merchant.id)
    ).all()
    return [
        {
            "id": str(row.id),
            "domain": row.domain,
            "status": row.status,
            "dns_records": row.dns_records,
            "local_part": row.local_part,
        }
        for row in rows
    ]


@router.post("/sending-domains", status_code=status.HTTP_201_CREATED)
def add_sending_domain(
    domain: str,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("merchant.write"))],
    local_part: str = "accounts",
) -> dict[str, Any]:
    normalized = domain.strip().casefold()
    if not normalized or "." not in normalized:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A valid domain is required")
    normalized_local = local_part.strip().casefold()
    if not normalized_local or not normalized_local.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid sender local part")
    token = secrets.token_urlsafe(24)
    try:
        provider = provision_provider_domain(normalized)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    records = (
        provider.records
        if provider is not None
        else [{"type": "TXT", "name": f"_vasooli.{normalized}", "value": token}]
    )
    row = SendingDomain(
        merchant_id=context.merchant.id,
        domain=normalized,
        verification_token=token,
        provider_domain_id=provider.provider_id if provider else None,
        local_part=normalized_local,
        status=("verified" if provider and provider.status == "verified" else "pending"),
        verified_at=(utcnow() if provider and provider.status == "verified" else None),
        dns_records=records,
    )
    session.add(row)
    session.commit()
    return {
        "id": str(row.id),
        "domain": row.domain,
        "status": row.status,
        "dns_records": row.dns_records,
        "local_part": row.local_part,
    }


@router.post("/sending-domains/{domain_id}/verify")
def verify_sending_domain(
    domain_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("merchant.write"))],
) -> dict[str, Any]:
    row = session.exec(
        select(SendingDomain).where(
            SendingDomain.id == domain_id,
            SendingDomain.merchant_id == context.merchant.id,
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sending domain not found")
    if row.provider_domain_id:
        try:
            provider = provider_domain_status(row.provider_domain_id)
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        verified = provider.status == "verified"
        records: list[Any] = provider.records
        if provider.records:
            row.dns_records = provider.records
    else:
        result = verify_domain_dns(row.domain, row.verification_token)
        verified = result.verified
        records = result.records
    row.status = "verified" if verified else "pending"
    row.verified_at = utcnow() if verified else None
    row.updated_at = utcnow()
    session.add(row)
    audit(
        session,
        action="sending_domain.verification_checked",
        merchant_id=context.merchant.id,
        actor_user_id=context.user.id,
        object_type="sending_domain",
        object_id=row.id,
        ip_address=client_ip(request),
        detail={"verified": verified, "records": records},
    )
    session.commit()
    return {"id": str(row.id), "status": row.status, "records": records}


def _policy_dict(row: ReminderPolicyVersion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "version": row.version,
        "tier_offsets": row.tier_offsets,
        "cooldown_days": row.cooldown_days,
        "max_attempts": row.max_attempts,
        "timezone": row.timezone,
        "channel": row.channel,
        "is_active": row.is_active,
    }
