"""Tenant membership, permissions, invitations, sessions and live audit records."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import (
    bool_column,
    fk_column,
    jsonb_column,
    pk_column,
    timestamp_column,
    updated_at_column,
)


class Permission(SQLModel, table=True):
    __tablename__ = "permissions"

    id: uuid.UUID = Field(sa_column=pk_column())
    codename: str = Field(sa_column=Column(String(100), nullable=False, unique=True, index=True))
    description: str = Field(sa_column=Column(String(300), nullable=False))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class Role(SQLModel, table=True):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("merchant_id", "slug", name="uq_roles_merchant_slug"),)

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    name: str = Field(sa_column=Column(String(100), nullable=False))
    slug: str = Field(sa_column=Column(String(80), nullable=False))
    description: str = Field(default="", sa_column=Column(String(300), nullable=False))
    is_system: bool = Field(default=True, sa_column=bool_column(default=True))
    is_immutable: bool = Field(default=True, sa_column=bool_column(default=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())


class RolePermission(SQLModel, table=True):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    role_id: uuid.UUID = Field(sa_column=fk_column("roles.id"))
    permission_id: uuid.UUID = Field(sa_column=fk_column("permissions.id"))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class UserPermissionOverride(SQLModel, table=True):
    """Reserved for explicit, audited exceptions to a role's permissions."""

    __tablename__ = "user_permission_overrides"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "merchant_id", "permission_id", name="uq_user_permission_override"
        ),
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_permission_override_effect"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    permission_id: uuid.UUID = Field(sa_column=fk_column("permissions.id"))
    effect: str = Field(sa_column=Column(String(10), nullable=False))
    reason: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    expires_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_by_user_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("users.id", nullable=True)
    )
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class MFAFactor(SQLModel, table=True):
    __tablename__ = "mfa_factors"

    id: uuid.UUID = Field(sa_column=pk_column())
    user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    factor_type: str = Field(sa_column=Column(String(30), nullable=False))
    label: str | None = Field(default=None, sa_column=Column(String(120), nullable=True))
    secret_encrypted: str | None = Field(default=None, sa_column=Column(String(512), nullable=True))
    is_primary: bool = Field(default=False, sa_column=bool_column(default=False))
    verified_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    last_used_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    revoked_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class MerchantMembership(SQLModel, table=True):
    __tablename__ = "merchant_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "merchant_id", name="uq_memberships_user_merchant"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    role_id: uuid.UUID = Field(sa_column=fk_column("roles.id"))
    is_active: bool = Field(default=True, sa_column=bool_column(default=True))
    invitation_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("merchant_invitations.id", nullable=True)
    )
    joined_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    revoked_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    updated_at: datetime = Field(sa_column=updated_at_column())


class MerchantInvitation(SQLModel, table=True):
    __tablename__ = "merchant_invitations"
    __table_args__ = (CheckConstraint("expires_at > created_at", name="ck_invitations_expiry"),)

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID = Field(sa_column=fk_column("merchants.id"))
    email: str = Field(sa_column=Column(String(320), nullable=False, index=True))
    role_id: uuid.UUID = Field(sa_column=fk_column("roles.id"))
    token_hash: str = Field(sa_column=Column(String(64), nullable=False, unique=True, index=True))
    invited_by_user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    expires_at: datetime = Field(sa_column=timestamp_column(index=True))
    accepted_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    revoked_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class UserSession(SQLModel, table=True):
    __tablename__ = "live_sessions"

    id: uuid.UUID = Field(sa_column=pk_column())
    user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    family_id: uuid.UUID = Field(index=True)
    refresh_token_hash: str = Field(
        sa_column=Column(String(64), nullable=False, unique=True, index=True)
    )
    replaced_by_session_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("live_sessions.id", nullable=True)
    )
    user_agent: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    ip_address: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    expires_at: datetime = Field(sa_column=timestamp_column(index=True))
    last_used_at: datetime = Field(sa_column=timestamp_column(default_now=True))
    revoked_at: datetime | None = Field(sa_column=timestamp_column(nullable=True, index=True))
    revoke_reason: str | None = Field(default=None, sa_column=Column(String(100), nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class AuthToken(SQLModel, table=True):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('verify_email', 'password_reset')",
            name="ck_auth_tokens_purpose",
        ),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    user_id: uuid.UUID = Field(sa_column=fk_column("users.id"))
    purpose: str = Field(sa_column=Column(String(30), nullable=False, index=True))
    token_hash: str = Field(sa_column=Column(String(64), nullable=False, unique=True, index=True))
    expires_at: datetime = Field(sa_column=timestamp_column(index=True))
    used_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True))


class AuthEvent(SQLModel, table=True):
    __tablename__ = "auth_events"

    id: uuid.UUID = Field(sa_column=pk_column())
    user_id: uuid.UUID | None = Field(default=None, sa_column=fk_column("users.id", nullable=True))
    email: str | None = Field(
        default=None, sa_column=Column(String(320), nullable=True, index=True)
    )
    event_type: str = Field(sa_column=Column(String(80), nullable=False, index=True))
    success: bool = Field(default=False, sa_column=bool_column(default=False))
    ip_address: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    user_agent: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    id: uuid.UUID = Field(sa_column=pk_column())
    merchant_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("merchants.id", nullable=True)
    )
    actor_user_id: uuid.UUID | None = Field(
        default=None, sa_column=fk_column("users.id", nullable=True)
    )
    action: str = Field(sa_column=Column(String(100), nullable=False, index=True))
    object_type: str | None = Field(default=None, sa_column=Column(String(80), nullable=True))
    object_id: uuid.UUID | None = Field(default=None, index=True)
    request_id: str | None = Field(default=None, sa_column=Column(String(100), nullable=True))
    source: str = Field(default="api", sa_column=Column(String(40), nullable=False))
    ip_address: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    created_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))
