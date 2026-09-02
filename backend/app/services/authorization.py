"""Fail-closed live tenant and permission resolution."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import event, text
from sqlmodel import select

from app.core.clock import utcnow
from app.core.db import SessionDep
from app.core.security import verify_session_token
from app.models import (
    Merchant,
    MerchantMembership,
    Permission,
    RolePermission,
    User,
    UserSession,
)
from app.services.reauth import consume_challenge

LIVE_ACCESS_COOKIE = "vasooli_live_access"
LIVE_REFRESH_COOKIE = "vasooli_live_refresh"


@dataclass(frozen=True)
class LiveContext:
    user: User
    merchant: Merchant
    membership: MerchantMembership
    session: UserSession
    permission: str


def set_merchant_context(session, merchant_id: uuid.UUID) -> None:
    """Set the transaction-local RLS tenant after an authorization decision."""
    session.exec(
        text("SELECT set_config('app.merchant_id', :merchant_id, true)").bindparams(
            merchant_id=str(merchant_id)
        )
    )


@contextmanager
def _sticky_setting(session, name: str, value: str, cleared: str) -> Iterator[None]:
    """Hold a transaction-local Postgres setting across the commits inside a block.

    `set_config(..., true)` is scoped to the current transaction, which is what stops
    it riding a pooled connection into somebody else's request. It also means the
    setting dies at the next `commit()` — and the recovery cycle commits after every
    invoice, so a one-shot call would cover the first invoice and silently stop
    applying from the second onward. That is a worse failure than not setting it at
    all, because the first row makes it look like it works.

    Re-applying from an `after_begin` hook covers every transaction the block opens,
    for exactly as long as the block is held, and gives up nothing: the setting is
    still transaction-local, and still gone once the block exits.

    Re-entrant per setting name, and nesting a different value for the same name is
    refused rather than silently ignored — two merchants sharing one transaction is
    the exact confusion this whole mechanism exists to prevent.
    """
    held: dict[str, tuple[str, int]] = getattr(session, "_vasooli_scopes", None) or {}
    session._vasooli_scopes = held
    current, depth = held.get(name, ("", 0))
    if depth and current != value:
        raise RuntimeError(f"{name} is already scoped to {current!r}; cannot nest {value!r}")

    def _apply(_session, _transaction, connection) -> None:
        connection.exec_driver_sql(f"SELECT set_config('{name}', '{value}', true)")

    held[name] = (value, depth + 1)
    if depth == 0:
        event.listen(session, "after_begin", _apply)
        # A transaction may already be open on entry; the hook only fires for the next.
        session.exec(text(f"SELECT set_config('{name}', :value, true)").bindparams(value=value))
    try:
        yield
    finally:
        value_held, depth_held = held[name]
        held[name] = (value_held, depth_held - 1)
        if depth_held - 1 == 0:
            held.pop(name, None)
            event.remove(session, "after_begin", _apply)
            session.exec(
                text(f"SELECT set_config('{name}', :value, true)").bindparams(value=cleared)
            )


def merchant_scope(session, merchant_id: uuid.UUID):
    """Pin the RLS tenant for a block of work that commits more than once.

    `set_merchant_context` is right for a request: one transaction, one commit at the
    end. Background work is not shaped that way. The recovery cycle commits after the
    diagnosis and again after the send, and delivery commits several times of its own
    inside that — so the tenant set at the top of an invoice is gone by the time the
    reminder row is written, and every write after the first is refused by the policy's
    `WITH CHECK`. Holding the tenant for the whole invoice is what makes the cycle
    able to write at all under a role that does not bypass row-level security.
    """
    return _sticky_setting(session, "app.merchant_id", str(merchant_id), "")


@contextmanager
def service_scope(session) -> Iterator[None]:
    """Read across tenants for work that has no request and no single merchant.

    Three kinds of code need this and cannot get it from `set_merchant_context`:

    * the recovery cycle and the retry sweeps, which walk every merchant's ledger;
    * webhook routing, which has to find the invoice a payment link or reply token
      belongs to *before* it can know whose invoice it is;
    * reconciliation against Razorpay, for the same reason.

    Without it those queries return zero rows the moment the application connects as a
    role that does not bypass row-level security — which is the role the deployment is
    supposed to use. Nothing raises: the cycle reports a clean run over no invoices and
    a real payment is never applied. Silent is the dangerous part, so this is deliberate
    and named rather than left to a superuser connection to paper over.

    The grant is read-only by construction. Every policy's `WITH CHECK` still demands a
    real `app.merchant_id`, so a service-scoped transaction cannot write a row into
    another tenant. Callers that mutate must still call `set_merchant_context` for the
    merchant they are acting on — as the Razorpay handler does once it has matched.

    Held across commits and re-entrant — see `_sticky_setting` for why both matter.
    """
    with _sticky_setting(session, "app.service_role", "true", "false"):
        yield


def live_access_subject(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    return f"live~{user_id}~{session_id}"


def parse_live_access(token: str | None) -> tuple[uuid.UUID, uuid.UUID] | None:
    subject = verify_session_token(token)
    if not subject:
        return None
    parts = subject.split("~")
    if len(parts) != 3 or parts[0] != "live":
        return None
    try:
        return uuid.UUID(parts[1]), uuid.UUID(parts[2])
    except ValueError:
        return None


def _has_permission(session, membership: MerchantMembership, codename: str) -> bool:
    return (
        session.exec(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(
                RolePermission.role_id == membership.role_id,
                Permission.codename == codename,
            )
        ).first()
        is not None
    )


def require_live_permission(codename: str):
    def dependency(
        request: Request,
        session: SessionDep,
        x_merchant_id: Annotated[str | None, Header(alias="X-Merchant-ID")] = None,
        access_token: Annotated[str | None, Cookie(alias=LIVE_ACCESS_COOKIE)] = None,
    ) -> LiveContext:
        identity = parse_live_access(access_token)
        if identity is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Live authentication required")
        if not x_merchant_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-Merchant-ID is required")
        try:
            merchant_id = uuid.UUID(x_merchant_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid merchant context") from exc

        user_id, session_id = identity
        user = session.get(User, user_id)
        live_session = session.get(UserSession, session_id)
        now: datetime = utcnow()
        if (
            user is None
            or user.status != "active"
            or not user.is_email_verified
            or live_session is None
            or live_session.user_id != user.id
            or live_session.revoked_at is not None
            or live_session.expires_at <= now
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Live session is not active")

        # This runs BEFORE set_merchant_context below — it is the check that decides
        # whether this user may act as that tenant at all, so it cannot assume the
        # context it is about to establish. merchant_memberships has RLS FORCED, so
        # without the service scope it matches nothing and every authenticated live
        # request 404s as "Merchant not found".
        #
        # Reading across tenants here is safe and necessary: the row is then required
        # to name this exact user AND this exact merchant, so a membership belonging
        # to anyone else cannot satisfy it.
        with service_scope(session):
            membership = session.exec(
                select(MerchantMembership).where(
                    MerchantMembership.user_id == user.id,
                    MerchantMembership.merchant_id == merchant_id,
                    MerchantMembership.is_active.is_(True),  # type: ignore[union-attr]
                )
            ).first()
        merchant = session.get(Merchant, merchant_id)
        # The same 404 covers an unknown merchant and another tenant's merchant.
        if membership is None or merchant is None or merchant.is_demo or merchant.mode != "live":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Merchant not found")
        if merchant.status in {"suspended", "closed"}:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Merchant is not active")
        if not _has_permission(session, membership, codename):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {codename}")

        # PostgreSQL RLS policies read this transaction-local value. It cannot leak
        # through the connection pool after commit/rollback.
        set_merchant_context(session, merchant.id)
        request.state.live_user_id = str(user.id)
        request.state.merchant_id = str(merchant.id)
        return LiveContext(user, merchant, membership, live_session, codename)

    return dependency


def require_live_reauth(codename: str):
    """Require a fresh password/MFA-backed re-auth proof for sensitive actions.

    The challenge is single-use and bound to the authenticated user. Keeping this as
    a dependency makes it difficult for a new sensitive endpoint to accidentally omit
    the second factor while retaining the normal merchant permission checks.
    """
    permission_dependency = require_live_permission(codename)

    def dependency(
        session: SessionDep,
        context: LiveContext = Depends(permission_dependency),
        x_reauth_token: Annotated[str | None, Header(alias="X-Reauth-Token")] = None,
    ) -> LiveContext:
        if not x_reauth_token or not consume_challenge(session, context.user, x_reauth_token):
            raise HTTPException(
                status.HTTP_428_PRECONDITION_REQUIRED,
                "Recent re-authentication is required for this action",
            )
        # Burning the challenge has to be durable even if the action then fails, so
        # this commits. That commit is also what makes the line below necessary:
        # `set_merchant_context` uses set_config(..., true), which is transaction-local
        # and dies here. Without re-applying it the handler runs with no tenant, and
        # every RLS policy's WITH CHECK refuses the write — so the endpoints behind
        # this dependency, the ones that create subscriptions and store payment
        # credentials, were the only ones that could not write.
        session.commit()
        set_merchant_context(session, context.merchant.id)
        return context

    return dependency


def get_scoped_object(session, model, object_id: uuid.UUID, merchant_id: uuid.UUID):
    """Load tenant-owned objects by both keys so foreign IDs do not become an IDOR."""

    return session.exec(
        select(model).where(model.id == object_id, model.merchant_id == merchant_id)
    ).first()


def require_active_subscription(
    session: SessionDep,
    context: Annotated[LiveContext, Depends(require_live_permission("merchant.read"))],
) -> LiveContext:
    """Refuse the workspace until the merchant's subscription is live.

    Registration used to hand out a fully working workspace before any payment
    instrument had been seen, so the first time a card was tested was the day the
    trial ended and the first real charge failed. A trial is now something a merchant
    enters by confirming a mandate, and this is the gate that makes that true for
    reads as well as writes.

    Deliberately not applied to `/api/live/auth` or `/api/live/billing`: a merchant
    who cannot reach billing can never pay, which would make the gate permanent.

    The seeded demo is exempt inside `subscription_state` — it has no subscription
    and never will, and gating it would lock a reviewer out of the one workspace that
    exists to be looked at.

    402 rather than 403: this is not a permission the merchant lacks, it is a payment
    the workspace is waiting for, and the client uses that distinction to route them
    to plan selection instead of showing an access error.
    """
    from app.services.billing import subscription_state

    state = subscription_state(session, context.merchant.id)
    if not state.is_active:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            state.paused_reason or "This workspace has no active subscription.",
        )
    return context
