"""Live identity lifecycle and rotating refresh-token families."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import Session, select

from app.core.clock import utcnow
from app.core.passwords import hash_live_password
from app.models import (
    AuditEvent,
    AuthEvent,
    AuthToken,
    Merchant,
    Permission,
    Role,
    RolePermission,
    User,
    UserSession,
)

ACCESS_TTL_SECONDS = 15 * 60
REFRESH_TTL_DAYS = 30
VERIFY_TTL_MINUTES = 15
RESET_TTL_MINUTES = 30

PERMISSIONS: dict[str, str] = {
    "merchant.read": "View merchant settings",
    "merchant.write": "Change merchant settings",
    "team.read": "View team members and invitations",
    "team.invite": "Invite team members",
    "team.manage": "Change or revoke memberships",
    "invoice.read": "View invoices",
    "invoice.write": "Change invoices",
    "invoice.import": "Import invoices",
    "customer.read": "View customers",
    "customer.write": "Change customers",
    "reminder.read": "View reminders",
    "reminder.send": "Send reminders",
    "reminder.pause": "Pause recovery",
    "reminder.configure": "Configure reminder policy",
    "payment_link.create": "Create payment links",
    "payment_link.read": "View payment links",
    "payment_link.refund": "Refund a payment",
    "erp.read": "View ERP connection state",
    "erp.sync": "Run ERP synchronization",
    "erp.configure": "Configure ERP connections",
    "billing.read": "View Vasooli billing",
    "billing.manage": "Manage Vasooli billing",
    "audit.read": "View audit events",
    "audit.export": "Export audit events",
    "support.break_glass": "Use time-limited support access",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": set(PERMISSIONS),
    "admin": set(PERMISSIONS) - {"billing.manage", "support.break_glass"},
    "collector": {
        "merchant.read",
        "invoice.read",
        "invoice.write",
        "invoice.import",
        "customer.read",
        "customer.write",
        "reminder.read",
        "reminder.send",
        "reminder.pause",
        "payment_link.create",
        "payment_link.read",
        "erp.read",
    },
    "analyst": {
        "merchant.read",
        "invoice.read",
        "customer.read",
        "reminder.read",
        "payment_link.read",
        "erp.read",
        "billing.read",
        "audit.read",
        "audit.export",
    },
    "billing-manager": {"merchant.read", "billing.read", "billing.manage"},
}


class LiveAuthError(ValueError):
    pass


@dataclass(frozen=True)
class IssuedSession:
    session: UserSession
    refresh_token: str


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def bootstrap_roles(session: Session, merchant: Merchant) -> dict[str, Role]:
    existing_permissions = {p.codename: p for p in session.exec(select(Permission)).all()}
    for codename, description in PERMISSIONS.items():
        if codename not in existing_permissions:
            permission = Permission(codename=codename, description=description)
            session.add(permission)
            session.flush()
            existing_permissions[codename] = permission

    roles: dict[str, Role] = {}
    for slug, codenames in ROLE_PERMISSIONS.items():
        role = Role(
            merchant_id=merchant.id,
            name=slug.replace("-", " ").title(),
            slug=slug,
            description=f"Vasooli system role: {slug}",
        )
        session.add(role)
        session.flush()
        roles[slug] = role
        for codename in sorted(codenames):
            session.add(
                RolePermission(
                    role_id=role.id,
                    permission_id=existing_permissions[codename].id,
                )
            )
    session.flush()
    return roles


def create_auth_token(session: Session, user: User, purpose: str) -> str:
    now = utcnow()
    for current in session.exec(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),  # type: ignore[union-attr]
        )
    ).all():
        current.used_at = now
        session.add(current)

    # Email verification is intentionally a short, human-entered code. Keep its
    # hash globally unique so the existing token lookup remains unambiguous, and
    # retain high-entropy opaque tokens for password reset links.
    if purpose == "verify_email":
        while True:
            raw = f"{secrets.randbelow(1_000_000):06d}"
            if (
                session.exec(
                    select(AuthToken.id).where(AuthToken.token_hash == token_hash(raw))
                ).first()
                is None
            ):
                break
    else:
        raw = new_opaque_token()
    ttl = (
        timedelta(minutes=VERIFY_TTL_MINUTES)
        if purpose == "verify_email"
        else timedelta(minutes=RESET_TTL_MINUTES)
    )
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=token_hash(raw),
            expires_at=now + ttl,
        )
    )
    return raw


def consume_auth_token(session: Session, raw: str, purpose: str) -> User:
    now = utcnow()
    record = session.exec(
        select(AuthToken).where(
            AuthToken.token_hash == token_hash(raw),
            AuthToken.purpose == purpose,
        )
    ).first()
    if record is None or record.used_at is not None or record.expires_at <= now:
        raise LiveAuthError("Token is invalid or expired")
    user = session.get(User, record.user_id)
    if user is None or user.status == "deleted":
        raise LiveAuthError("Token is invalid or expired")
    record.used_at = now
    session.add(record)
    return user


def issue_session(
    session: Session,
    user: User,
    *,
    ip_address: str | None,
    user_agent: str | None,
    family_id: uuid.UUID | None = None,
) -> IssuedSession:
    raw = new_opaque_token()
    row = UserSession(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        refresh_token_hash=token_hash(raw),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
        expires_at=utcnow() + timedelta(days=REFRESH_TTL_DAYS),
    )
    session.add(row)
    session.flush()
    return IssuedSession(session=row, refresh_token=raw)


def rotate_refresh_token(
    session: Session,
    raw: str,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[User, IssuedSession]:
    now = utcnow()
    current = session.exec(
        select(UserSession).where(UserSession.refresh_token_hash == token_hash(raw))
    ).first()
    if current is None:
        raise LiveAuthError("Refresh token is invalid")

    if current.revoked_at is not None:
        # A rotated token being presented again is theft/replay until proven
        # otherwise. Revoke the entire family, including the attacker's new token.
        for member in session.exec(
            select(UserSession).where(UserSession.family_id == current.family_id)
        ).all():
            if member.revoked_at is None:
                member.revoked_at = now
                member.revoke_reason = "refresh_token_reuse"
                session.add(member)
        session.add(
            AuthEvent(
                user_id=current.user_id,
                event_type="refresh_token_reuse",
                success=False,
                ip_address=ip_address,
            )
        )
        session.commit()
        raise LiveAuthError("Refresh token reuse detected; session family revoked")
    if current.expires_at <= now:
        current.revoked_at = now
        current.revoke_reason = "expired"
        session.add(current)
        raise LiveAuthError("Refresh token is expired")

    user = session.get(User, current.user_id)
    if user is None or user.status != "active" or not user.is_email_verified:
        raise LiveAuthError("User session is not active")

    issued = issue_session(
        session,
        user,
        ip_address=ip_address,
        user_agent=user_agent,
        family_id=current.family_id,
    )
    current.revoked_at = now
    current.revoke_reason = "rotated"
    current.replaced_by_session_id = issued.session.id
    current.last_used_at = now
    session.add(current)
    return user, issued


def revoke_user_sessions(session: Session, user_id: uuid.UUID, reason: str) -> int:
    count = 0
    now = utcnow()
    for row in session.exec(
        select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).all():
        row.revoked_at = now
        row.revoke_reason = reason
        session.add(row)
        count += 1
    return count


def audit(
    session: Session,
    *,
    action: str,
    merchant_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    detail: dict | None = None,
) -> None:
    session.add(
        AuditEvent(
            merchant_id=merchant_id,
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            ip_address=ip_address,
            detail=detail or {},
        )
    )


def create_live_user(email: str, password: str, display_name: str | None = None) -> User:
    return User(
        email=normalize_email(email),
        display_name=display_name,
        password_hash=hash_live_password(password),
    )


def verify_reviewer_account() -> None:
    """Refuse to serve if the reviewer door would not be read-only.

    `REVIEWER_ACCESS_ENABLED` hands a session to anyone who clicks, without a
    credential. That is safe only because the account behind it is an `auditor`, and
    `app.api.deps` rejects every non-GET/HEAD/OPTIONS request from an auditor. If the
    account were given a writing role — by hand, by a seed script, by a mistake — the
    button would still say "read-only demo" while granting write access to the
    operator console.

    Checked at startup rather than trusted, because the failure is silent and the
    button is public.
    """
    from sqlmodel import Session, select

    from app.core.config import settings
    from app.core.db import engine
    from app.models import OperatorAccount

    with Session(engine) as session:
        account = session.exec(
            select(OperatorAccount).where(OperatorAccount.username == settings.reviewer_username)
        ).first()

    if account is None:
        raise RuntimeError(
            f"REVIEWER_ACCESS_ENABLED is true but no operator account named "
            f"'{settings.reviewer_username}' exists. Create it with scripts.manage_operator, "
            "or disable reviewer access."
        )
    if not account.is_active:
        raise RuntimeError(
            f"Reviewer account '{settings.reviewer_username}' is inactive. "
            "Reactivate it or disable REVIEWER_ACCESS_ENABLED."
        )
    if account.role != "auditor":
        raise RuntimeError(
            f"Reviewer account '{settings.reviewer_username}' has role '{account.role}', not "
            "'auditor'. Reviewer access is a public door and must be read-only; refusing to "
            "start rather than grant write access behind a button labelled as a demo."
        )
