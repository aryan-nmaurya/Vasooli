"""Pruning of expired ephemeral auth material.

Five tables carry an `expires_at` and nothing ever removed a row from any of them.
Sessions and refresh tokens are written on every login, one-time OAuth states on every
connect attempt, re-auth challenges on every sensitive action. None of them is read
once expired, and all of them are read on the hot path — so they grow forever while
making the queries that matter slower, and they bloat every backup and restore drill
along the way.

Two deliberate exclusions, because "delete expired rows" is the wrong rule for them:

* **Audit tables are never pruned.** `audit_events` and `audit_logs` are append-only by
  database trigger, and their value is precisely that nothing removes them.
* **Expired suppressions and invitations stay.** An expired suppression is a record
  that this address once bounced or opted out, and an expired invitation is a record
  that someone was invited. Both are evidence rather than scratch state, and both are
  low-volume.

The grace window exists so a security question asked next week can still be answered.
An investigation into "which sessions were active when this happened" is worthless if
the rows were deleted the moment they expired.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlmodel import Session

from app.core.clock import utcnow
from app.core.logging import get_logger

log = get_logger("retention")

#: Kept past expiry so recent history is still available to an investigation.
SESSION_GRACE_DAYS = 30
#: Single-use and short-lived by design; nothing looks at them after the fact.
ONE_TIME_GRACE_DAYS = 7

#: Deleted with a plain `expires_at` cutoff. Ordered so children go before parents.
_PRUNABLE: tuple[tuple[str, int], ...] = (
    ("auth_tokens", SESSION_GRACE_DAYS),
    ("live_sessions", SESSION_GRACE_DAYS),
    ("oauth_states", ONE_TIME_GRACE_DAYS),
    ("reauth_challenges", ONE_TIME_GRACE_DAYS),
)


@dataclass
class PruneReport:
    deleted: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.deleted.values())


def prune_expired(session: Session, *, batch_limit: int = 10_000) -> PruneReport:
    """Delete expired ephemeral rows past their grace window.

    Bounded per table per run. An unbounded `DELETE` on a table that has gone
    unpruned for a year takes a long lock and can time out mid-statement, which is a
    worse outage than the bloat it was meant to fix — so this removes at most
    `batch_limit` rows each pass and lets the next run continue.
    """
    now = utcnow()
    deleted: dict[str, int] = {}

    for table, grace_days in _PRUNABLE:
        cutoff = now - timedelta(days=grace_days)
        result = session.exec(
            text(
                f"""
                DELETE FROM {table}
                WHERE ctid IN (
                    SELECT ctid FROM {table}
                    WHERE expires_at IS NOT NULL AND expires_at < :cutoff
                    LIMIT :limit
                )
                """  # noqa: S608 - table names come from the module constant above
            ).bindparams(cutoff=cutoff, limit=batch_limit)
        )
        count = result.rowcount or 0
        if count:
            deleted[table] = count

    if deleted:
        log.info("retention.pruned", **deleted)
    return PruneReport(deleted=deleted)
