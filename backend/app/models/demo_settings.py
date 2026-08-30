"""Runtime settings a reviewer can change. Demo controls.

The cadence is measured in days past due — 3, 10, 21 — which is correct for a real
merchant and useless in a demo nobody will watch for three weeks. This row holds how
far ahead of real time the simulated clock runs, so the whole schedule can be walked
in a couple of minutes.

Deliberately a database row rather than an environment variable. `DEMO_TIME_OFFSET_DAYS`
already exists for that, and `assert_production_safe` refuses to start with it set —
correctly, because a stale offset baked into a real deployment silently corrupts
overdue maths for real invoices. This is the opposite shape: nothing is set at boot,
every change is made deliberately through an audited endpoint, and it can be wound
back to zero without a redeploy.

One row, forever. `id` is pinned to 1 by a check constraint so a second row cannot be
created and quietly become the one nobody is reading.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from app.models.base import timestamp_column

SINGLETON_ID = 1


class DemoSettings(SQLModel, table=True):
    __tablename__ = "demo_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_demo_settings_singleton"),
        # A demo that has run 10 years into the future is a bug, not a demo. The cap
        # keeps a runaway auto-advance from producing invoices absurdly overdue.
        CheckConstraint(
            "offset_days >= 0 AND offset_days <= 3650", name="ck_demo_settings_offset_range"
        ),
    )

    id: int = Field(default=SINGLETON_ID, primary_key=True)

    #: Days the simulated clock runs ahead of real time. Zero means the demo clock is
    #: off and every time read is ordinary wall-clock time.
    offset_days: int = 0

    #: Who moved it last, as "human:<username>", so the audit trail and this row agree.
    updated_by: str | None = None
    updated_at: datetime = Field(sa_column=timestamp_column(default_now=True))

    #: The real instant the clock was last reset to zero. Shown in the UI so a
    #: reviewer can see how long the current simulated run has been going.
    started_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))

    #: Where reminder mail goes instead of the customer, overriding EMAIL_REDIRECT_TO.
    #:
    #: Exists so a reviewer can point the demo at their own inbox, receive a real
    #: reminder, and reply to it — the only way to exercise the inbound path without
    #: access to the deployment's environment. Null means the environment default
    #: applies. It can never disable redirection; see
    #: app.core.runtime.effective_email_redirect.
    email_redirect_override: str | None = None

    @property
    def is_running(self) -> bool:
        return self.offset_days > 0
