"""One row per scheduled job execution.

"Is the agent running?" was previously answerable only from application logs, and the
dashboard could report the scheduler's *configuration* — which says nothing about
whether a cycle has actually executed. A scheduler embedded in the API process can die
quietly: the API keeps answering health checks, invoices keep ageing, and nothing is
chased. Configuration said "enabled" the whole time.

This table is the evidence. A run is recorded when it starts and updated when it
finishes, so a job that hangs is visible as a run that started and never ended — the
failure mode a success-only log cannot show.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.base import jsonb_column, pk_column, timestamp_column


class JobStatus:
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


#: How stale a job's last success may get before the dashboard calls it unhealthy.
#:
#: Deliberately generous multiples of each schedule. A daily job that ran 25 hours ago
#: is late but not necessarily broken — a deploy at the wrong minute does that — while
#: one that last ran three days ago is a stopped agent. Alerting on the first would
#: train an operator to ignore the banner, which is worse than not having one.
STALE_AFTER_SECONDS: dict[str, int] = {
    "recovery_cycle": 30 * 3600,  # daily at 10:00 IST
    "payment_link_sync": 3 * 3600,  # hourly
    "retry_operations": 15 * 60,  # every minute
    "service_heartbeat": 30 * 60,  # every five minutes
}


class JobRun(SQLModel, table=True):
    __tablename__ = "job_runs"

    id: uuid.UUID = Field(sa_column=pk_column())
    job_id: str = Field(index=True)
    status: str = Field(default=JobStatus.RUNNING, index=True)

    started_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))
    finished_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    duration_ms: int | None = None

    #: The job's own report — what the recovery cycle sent, retried, escalated. Kept so
    #: "it ran" and "it did something" are separately answerable.
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=jsonb_column(default=dict))
    error: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None
