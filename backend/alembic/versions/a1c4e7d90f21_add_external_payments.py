"""add external payments and split the invoice paid columns

Money that arrived outside a Vasooli payment link previously had nowhere to go, so a
customer who paid by NEFT stayed overdue and kept being chased.

The column split is the load-bearing half. Reconciliation applies Razorpay's running
total with `max()`, which is what makes duplicate and out-of-order webhooks harmless.
That is only correct about a figure which really is a restatement of one running total,
so operator-entered payments get their own additive column and `amount_paid_paise`
becomes the sum. Every existing rupee came from a payment link, so the backfill puts
the current total into `link_paid_paise` and leaves the external column at zero.

Revision ID: a1c4e7d90f21
Revises: 41d887285edd
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c4e7d90f21"
down_revision: str | Sequence[str] | None = "41d887285edd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Added nullable, backfilled, then made NOT NULL: an ALTER that adds a NOT NULL
    # column with a server default rewrites the table under an exclusive lock, which is
    # avoidable here and unpleasant on a live ledger.
    op.add_column("invoices", sa.Column("link_paid_paise", sa.BigInteger(), nullable=True))
    op.add_column("invoices", sa.Column("external_paid_paise", sa.BigInteger(), nullable=True))
    op.execute("UPDATE invoices SET link_paid_paise = amount_paid_paise, external_paid_paise = 0")
    op.alter_column("invoices", "link_paid_paise", nullable=False)
    op.alter_column("invoices", "external_paid_paise", nullable=False)
    op.create_check_constraint(
        "ck_invoices_link_paid_non_negative", "invoices", "link_paid_paise >= 0"
    )
    op.create_check_constraint(
        "ck_invoices_external_paid_non_negative", "invoices", "external_paid_paise >= 0"
    )

    op.create_table(
        "external_payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("reference", sa.String(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by", sa.String(), nullable=True),
        sa.Column("reversal_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount_paise > 0", name="ck_external_payments_amount_positive"),
        # The same bank transfer can settle two invoices, so this is scoped to the
        # invoice. Within one invoice it refuses the ordinary way a payment gets
        # double-counted: the same UTR entered twice.
        sa.UniqueConstraint(
            "invoice_id", "reference", name="uq_external_payments_invoice_reference"
        ),
    )
    op.create_index("ix_external_payments_invoice_id", "external_payments", ["invoice_id"])
    op.create_index("ix_external_payments_reference", "external_payments", ["reference"])
    op.create_index("ix_external_payments_recorded_at", "external_payments", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_external_payments_recorded_at", table_name="external_payments")
    op.drop_index("ix_external_payments_reference", table_name="external_payments")
    op.drop_index("ix_external_payments_invoice_id", table_name="external_payments")
    op.drop_table("external_payments")
    op.drop_constraint("ck_invoices_external_paid_non_negative", "invoices", type_="check")
    op.drop_constraint("ck_invoices_link_paid_non_negative", "invoices", type_="check")
    op.drop_column("invoices", "external_paid_paise")
    op.drop_column("invoices", "link_paid_paise")
