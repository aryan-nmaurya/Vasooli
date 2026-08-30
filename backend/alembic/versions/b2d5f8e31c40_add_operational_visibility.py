"""add job runs, email delivery events and inbound retry state

Three of the audit's operational blind spots, all the same shape: something happened
(or did not happen) and there was no durable record an operator could look at.

* `job_runs` — proof a scheduled cycle actually executed. The dashboard could report
  the scheduler's configuration, which says nothing about execution; an APScheduler
  thread dying inside Uvicorn leaves the API healthy and nothing chased.
* `email_events` plus the reminder delivery columns — what the mail provider says
  happened AFTER it accepted the message. `sent_at` was stamped on a 2xx from the API,
  which is custody, not delivery.
* inbound retry state — a stored customer reply whose processing raised had no way back
  in: the webhook already answered 200, so the provider never redelivered it.

Revision ID: b2d5f8e31c40
Revises: a1c4e7d90f21
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2d5f8e31c40"
down_revision: str | Sequence[str] | None = "a1c4e7d90f21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Scheduler evidence -------------------------------------------------
    op.create_table(
        "job_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_runs_job_id", "job_runs", ["job_id"])
    op.create_index("ix_job_runs_status", "job_runs", ["status"])
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])

    # --- Provider delivery outcomes -----------------------------------------
    op.create_table(
        "email_events",
        sa.Column("id", sa.UUID(), nullable=False),
        # Both FKs nullable: a provider can report on a message whose reminder we no
        # longer hold, and dropping that event would lose a bounce.
        sa.Column("reminder_id", sa.UUID(), nullable=True),
        sa.Column("invoice_id", sa.UUID(), nullable=True),
        sa.Column("provider_event_id", sa.String(), nullable=False),
        sa.Column("provider_message_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reminder_id"], ["reminders.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique, and that is the deduplication. Providers deliver at-least-once exactly
    # like Razorpay does, and an in-memory set forgets on restart.
    op.create_index(
        "ix_email_events_provider_event_id", "email_events", ["provider_event_id"], unique=True
    )
    op.create_index("ix_email_events_provider_message_id", "email_events", ["provider_message_id"])
    op.create_index("ix_email_events_reminder_id", "email_events", ["reminder_id"])
    op.create_index("ix_email_events_invoice_id", "email_events", ["invoice_id"])
    op.create_index("ix_email_events_event_type", "email_events", ["event_type"])
    op.create_index("ix_email_events_state", "email_events", ["state"])
    op.create_index("ix_email_events_occurred_at", "email_events", ["occurred_at"])

    op.add_column("reminders", sa.Column("delivery_status", sa.String(), nullable=True))
    op.add_column("reminders", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("bounced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("delivery_detail", sa.String(), nullable=True))
    op.add_column(
        "reminders", sa.Column("last_delivery_event_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_reminders_delivery_status", "reminders", ["delivery_status"])

    # --- Inbound reprocessing ----------------------------------------------
    op.add_column(
        "inbound_messages",
        sa.Column("processing_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "inbound_messages", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "inbound_messages", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Existing rows that already failed have no schedule. Giving them one now makes the
    # sweep pick them up on its next pass rather than leaving them stranded — which is
    # the whole point of the change.
    op.execute(
        "UPDATE inbound_messages SET next_retry_at = now() "
        "WHERE processed_at IS NULL AND processing_error IS NOT NULL"
    )
    op.alter_column("inbound_messages", "processing_attempts", server_default=None)


def downgrade() -> None:
    op.drop_column("inbound_messages", "next_retry_at")
    op.drop_column("inbound_messages", "last_attempt_at")
    op.drop_column("inbound_messages", "processing_attempts")

    op.drop_index("ix_reminders_delivery_status", table_name="reminders")
    op.drop_column("reminders", "last_delivery_event_at")
    op.drop_column("reminders", "delivery_detail")
    op.drop_column("reminders", "bounced_at")
    op.drop_column("reminders", "delivered_at")
    op.drop_column("reminders", "delivery_status")

    for index in (
        "ix_email_events_occurred_at",
        "ix_email_events_state",
        "ix_email_events_event_type",
        "ix_email_events_invoice_id",
        "ix_email_events_reminder_id",
        "ix_email_events_provider_message_id",
        "ix_email_events_provider_event_id",
    ):
        op.drop_index(index, table_name="email_events")
    op.drop_table("email_events")

    op.drop_index("ix_job_runs_started_at", table_name="job_runs")
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_job_runs_job_id", table_name="job_runs")
    op.drop_table("job_runs")
