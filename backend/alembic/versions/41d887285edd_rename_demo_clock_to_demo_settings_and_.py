"""rename demo clock to demo settings and add email override

Revision ID: 41d887285edd
Revises: 1b0e325f394d
Create Date: 2026-08-30 10:06:29.560888

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # SQLModel emits sqlmodel.sql.sqltypes.AutoString in autogenerate

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "41d887285edd"
down_revision: str | Sequence[str] | None = "1b0e325f394d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The row always held runtime demo state; it now holds a mail destination too, so
    # the name `demo_clock` had become actively misleading. Renaming keeps the single
    # row and its constraints intact — no data moves.
    op.rename_table("demo_clock", "demo_settings")
    op.execute(
        "ALTER TABLE demo_settings RENAME CONSTRAINT ck_demo_clock_singleton TO ck_demo_settings_singleton"
    )
    op.execute(
        "ALTER TABLE demo_settings RENAME CONSTRAINT ck_demo_clock_offset_range TO ck_demo_settings_offset_range"
    )
    op.add_column(
        "demo_settings",
        sa.Column("email_redirect_override", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("demo_settings", "email_redirect_override")
    op.execute(
        "ALTER TABLE demo_settings RENAME CONSTRAINT ck_demo_settings_offset_range TO ck_demo_clock_offset_range"
    )
    op.execute(
        "ALTER TABLE demo_settings RENAME CONSTRAINT ck_demo_settings_singleton TO ck_demo_clock_singleton"
    )
    op.rename_table("demo_settings", "demo_clock")
