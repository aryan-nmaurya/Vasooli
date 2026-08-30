"""add live tenancy, identity, RBAC and tenant-safe invoice keys

Revision ID: e31f6a9c7d42
Revises: b2d5f8e31c40
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e31f6a9c7d42"
down_revision: str | Sequence[str] | None = "b2d5f8e31c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    # Expand existing merchant rows as explicit demo tenants. Python defaults keep
    # new demo seeds identical; server defaults make the backfill safe for deployed
    # databases and are removed where the application must make an explicit choice.
    op.add_column("merchants", sa.Column("legal_name", sa.String(240), nullable=True))
    op.add_column(
        "merchants", sa.Column("country", sa.String(2), nullable=False, server_default="IN")
    )
    op.add_column(
        "merchants",
        sa.Column("timezone", sa.String(80), nullable=False, server_default="Asia/Kolkata"),
    )
    op.add_column(
        "merchants", sa.Column("mode", sa.String(12), nullable=False, server_default="demo")
    )
    op.add_column(
        "merchants", sa.Column("status", sa.String(20), nullable=False, server_default="active")
    )
    op.add_column(
        "merchants", sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "merchants",
        sa.Column(
            "onboarding_state",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("merchants", sa.Column("terms_accepted_at", sa.DateTime(timezone=True)))
    op.add_column("merchants", sa.Column("privacy_accepted_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_merchants_mode", "merchants", "mode IN ('demo', 'live')")
    op.create_check_constraint(
        "ck_merchants_mode_flag",
        "merchants",
        "(mode = 'demo' AND is_demo) OR (mode = 'live' AND NOT is_demo)",
    )
    op.create_check_constraint(
        "ck_merchants_status",
        "merchants",
        "status IN ('onboarding', 'active', 'suspended', 'closed')",
    )

    # Replace globally unique invoice numbers and add a stable, unguessable inbound
    # identity. Existing demo rows retain their invoice numbers and legacy aliases.
    op.drop_index("ix_invoices_invoice_number", table_name="invoices")
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"])
    op.create_unique_constraint(
        "uq_invoices_merchant_number", "invoices", ["merchant_id", "invoice_number"]
    )
    op.add_column(
        "invoices",
        sa.Column(
            "reply_token",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.alter_column("invoices", "reply_token", server_default=None)
    op.create_index("ix_invoices_reply_token", "invoices", ["reply_token"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(160)),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_ip", sa.String(64)),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("mfa_metadata", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'deleted')", name="ck_users_status"
        ),
        sa.CheckConstraint("failed_login_attempts >= 0", name="ck_users_failed_login_attempts"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("codename", sa.String(100), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_permissions_codename", "permissions", ["codename"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("is_immutable", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "slug", name="uq_roles_merchant_slug"),
    )
    op.create_index("ix_roles_merchant_id", "roles", ["merchant_id"])

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_pair"),
    )
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"])
    op.create_index("ix_role_permissions_permission_id", "role_permissions", ["permission_id"])

    op.create_table(
        "user_permission_overrides",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.Column("effect", sa.String(10), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("effect IN ('allow', 'deny')", name="ck_permission_override_effect"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "merchant_id", "permission_id", name="uq_user_permission_override"
        ),
    )
    for column in ("user_id", "merchant_id", "permission_id", "created_by_user_id"):
        op.create_index(
            f"ix_user_permission_overrides_{column}", "user_permission_overrides", [column]
        )

    op.create_table(
        "mfa_factors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("factor_type", sa.String(30), nullable=False),
        sa.Column("label", sa.String(120)),
        sa.Column("secret_encrypted", sa.String(512)),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mfa_factors_user_id", "mfa_factors", ["user_id"])

    op.create_table(
        "merchant_invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expires_at > created_at", name="ck_invitations_expiry"),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_merchant_invitations_merchant_id", "merchant_invitations", ["merchant_id"])
    op.create_index("ix_merchant_invitations_email", "merchant_invitations", ["email"])
    op.create_index("ix_merchant_invitations_role_id", "merchant_invitations", ["role_id"])
    op.create_index(
        "ix_merchant_invitations_invited_by_user_id",
        "merchant_invitations",
        ["invited_by_user_id"],
    )
    op.create_index("ix_merchant_invitations_expires_at", "merchant_invitations", ["expires_at"])
    op.create_index(
        "ix_merchant_invitations_token_hash", "merchant_invitations", ["token_hash"], unique=True
    )

    op.create_table(
        "merchant_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("invitation_id", sa.UUID()),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["invitation_id"], ["merchant_invitations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "merchant_id", name="uq_memberships_user_merchant"),
    )
    for column in ("user_id", "merchant_id", "role_id", "invitation_id"):
        op.create_index(f"ix_merchant_memberships_{column}", "merchant_memberships", [column])

    op.create_table(
        "live_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("replaced_by_session_id", sa.UUID()),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["replaced_by_session_id"], ["live_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "family_id", "replaced_by_session_id", "expires_at", "revoked_at"):
        op.create_index(f"ix_live_sessions_{column}", "live_sessions", [column])
    op.create_index(
        "ix_live_sessions_refresh_token_hash", "live_sessions", ["refresh_token_hash"], unique=True
    )

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'password_reset')", name="ck_auth_tokens_purpose"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "purpose", "expires_at"):
        op.create_index(f"ix_auth_tokens_{column}", "auth_tokens", [column])
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"], unique=True)

    op.create_table(
        "auth_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID()),
        sa.Column("email", sa.String(320)),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "email", "event_type", "created_at"):
        op.create_index(f"ix_auth_events_{column}", "auth_events", [column])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("merchant_id", sa.UUID()),
        sa.Column("actor_user_id", sa.UUID()),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("object_type", sa.String(80)),
        sa.Column("object_id", sa.UUID()),
        sa.Column("request_id", sa.String(100)),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("merchant_id", "actor_user_id", "action", "object_id", "created_at"):
        op.create_index(f"ix_audit_events_{column}", "audit_events", [column])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION vasooli_audit_events_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION vasooli_audit_events_append_only();
        """
    )

    # The application sets app.merchant_id transaction-locally after authorizing the
    # user. Table owners retain their normal bypass so the frozen demo code path and
    # migrations continue to work; deployed application roles must be non-owners.
    for table in (
        "customers",
        "invoices",
        "roles",
        "merchant_invitations",
        "merchant_memberships",
        "user_permission_overrides",
        "audit_events",
    ):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'''CREATE POLICY {table}_merchant_isolation ON "{table}"
                USING (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)
                WITH CHECK (merchant_id = NULLIF(current_setting('app.merchant_id', true), '')::uuid)'''
        )
    op.execute(
        """CREATE POLICY merchant_invitations_token_lookup ON merchant_invitations
        FOR SELECT USING (
            token_hash = NULLIF(current_setting('app.invitation_token', true), '')
        )"""
    )


def downgrade() -> None:
    for table in (
        "audit_events",
        "merchant_memberships",
        "user_permission_overrides",
        "merchant_invitations",
        "roles",
        "invoices",
        "customers",
    ):
        op.execute(f'DROP POLICY IF EXISTS {table}_merchant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.execute("DROP POLICY IF EXISTS merchant_invitations_token_lookup ON merchant_invitations")

    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS vasooli_audit_events_append_only()")
    for table in (
        "audit_events",
        "auth_events",
        "auth_tokens",
        "live_sessions",
        "merchant_memberships",
        "merchant_invitations",
        "role_permissions",
        "roles",
        "permissions",
        "mfa_factors",
        "user_permission_overrides",
        "users",
    ):
        op.drop_table(table)

    op.drop_index("ix_invoices_reply_token", table_name="invoices")
    op.drop_column("invoices", "reply_token")
    op.drop_constraint("uq_invoices_merchant_number", "invoices", type_="unique")
    op.drop_index("ix_invoices_invoice_number", table_name="invoices")
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"], unique=True)

    op.drop_constraint("ck_merchants_status", "merchants", type_="check")
    op.drop_constraint("ck_merchants_mode_flag", "merchants", type_="check")
    op.drop_constraint("ck_merchants_mode", "merchants", type_="check")
    for column in (
        "privacy_accepted_at",
        "terms_accepted_at",
        "onboarding_state",
        "is_demo",
        "status",
        "mode",
        "timezone",
        "country",
        "legal_name",
    ):
        op.drop_column("merchants", column)
