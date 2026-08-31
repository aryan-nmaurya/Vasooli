"""add refund and chargeback collection adjustments"""

import sqlalchemy as sa

from alembic import op

revision = "f6b7c8d9e012"
down_revision = "e5a1c9b74f38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sending_domains",
        sa.Column("provider", sa.String(length=30), nullable=False, server_default="resend"),
    )
    op.add_column(
        "sending_domains", sa.Column("provider_domain_id", sa.String(length=160), nullable=True)
    )
    op.add_column(
        "sending_domains",
        sa.Column("local_part", sa.String(length=64), nullable=False, server_default="accounts"),
    )
    op.create_unique_constraint(
        "uq_sending_domains_provider_domain_id", "sending_domains", ["provider_domain_id"]
    )
    op.add_column(
        "invoices", sa.Column("refunded_paise", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.add_column(
        "invoices",
        sa.Column("chargeback_paise", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_invoices_refunded_non_negative", "invoices", "refunded_paise >= 0"
    )
    op.create_check_constraint(
        "ck_invoices_chargeback_non_negative", "invoices", "chargeback_paise >= 0"
    )
    op.add_column(
        "payment_links",
        sa.Column("amount_refunded_paise", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_payment_links_refunded_non_negative",
        "payment_links",
        "amount_refunded_paise >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_payment_links_refunded_non_negative", "payment_links", type_="check")
    op.drop_column("payment_links", "amount_refunded_paise")
    op.drop_constraint("ck_invoices_chargeback_non_negative", "invoices", type_="check")
    op.drop_constraint("ck_invoices_refunded_non_negative", "invoices", type_="check")
    op.drop_column("invoices", "chargeback_paise")
    op.drop_column("invoices", "refunded_paise")
    op.drop_constraint("uq_sending_domains_provider_domain_id", "sending_domains", type_="unique")
    op.drop_column("sending_domains", "local_part")
    op.drop_column("sending_domains", "provider_domain_id")
    op.drop_column("sending_domains", "provider")
