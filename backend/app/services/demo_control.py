"""The demo clock, and the audit trail behind it. Demo controls.

Why this exists: the cadence fires at 3, 10 and 21 days overdue, which is the right
schedule for a real merchant and unwatchable in a review. Moving a simulated clock
forward lets someone see the whole loop — first reminder, reply, promise, pause,
escalation, payment — in about two minutes, running the real code the whole way.

Three rules this module keeps:

**Nothing is faked.** Advancing the clock does not synthesise reminders or replies. It
moves time and runs the ordinary recovery cycle, which then decides for itself what is
due. What a reviewer watches is the production path reacting to a later date.

**Every move is audited.** The clock is a lever that changes what the system believes
about the present, so each change writes an audit row naming who moved it and by how
much. A trail that records the consequences but not the cause would be worse than none.

**It is off unless a deployment says otherwise.** DEMO_CONTROLS_ENABLED defaults to
false, so a real multi-merchant deployment cannot be nudged into a fictional present by
an endpoint someone forgot was there.
"""

from sqlmodel import Session

from app.core import clock, runtime
from app.core.clock import utcnow
from app.core.config import settings
from app.core.logging import get_logger
from app.models import AuditAction, AuditLog, DemoSettings
from app.models.demo_settings import SINGLETON_ID

log = get_logger("demo_control")

#: The largest single jump. Big enough to cross any tier boundary in one press
#: (21 days is the last one), small enough that a stuck auto-advance cannot run the
#: ledger years into the future before anyone notices.
MAX_ADVANCE_DAYS = 30


class DemoControlsDisabledError(RuntimeError):
    """Raised when the clock is touched on a deployment that did not opt in."""


def _require_enabled() -> None:
    if not settings.demo_controls_enabled:
        raise DemoControlsDisabledError(
            "Demo controls are disabled. Set DEMO_CONTROLS_ENABLED=true to enable the "
            "simulated clock on this deployment."
        )


def get_clock(session: Session) -> DemoSettings:
    """The singleton row, created on first use."""
    row = session.get(DemoSettings, SINGLETON_ID)
    if row is None:
        row = DemoSettings(id=SINGLETON_ID, offset_days=0)
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def load_into_clock(session: Session) -> int:
    """Restore the persisted overrides into module state. Called at startup.

    Without this a restart silently rewinds the demo to real time and drops the
    reviewer's mail destination in the middle of a review, which looks like the
    cadence spontaneously un-firing and the emails going missing.
    """
    if not settings.demo_controls_enabled:
        clock.set_runtime_offset(0)
        runtime.set_email_redirect_override(None)
        return 0
    row = get_clock(session)
    clock.set_runtime_offset(row.offset_days)
    runtime.set_email_redirect_override(row.email_redirect_override)
    return row.offset_days


def set_email_redirect(session: Session, *, address: str | None, actor: str) -> DemoSettings:
    """Point reminder mail at a reviewer's own inbox.

    Only moves the redirect; it cannot switch it off. Clearing falls back to
    EMAIL_REDIRECT_TO, so no sequence of calls here can start mailing the invented
    addresses in the seeded ledger.

    The address is audited rather than merely stored: reminder mail is the system's
    one outbound side effect, and a record of where it was sent — and who redirected
    it — is what makes that safe to hand to someone else.
    """
    _require_enabled()

    cleaned = (address or "").strip() or None
    # Deliberately shallow. The provider is the real validator; this only rejects
    # input that is obviously not an address, so a typo fails here rather than
    # silently swallowing every reminder for the rest of the session.
    if cleaned is not None and (
        len(cleaned) > 254 or "@" not in cleaned or cleaned.startswith("@")
    ):
        raise ValueError(f"{cleaned!r} does not look like an email address")

    row = get_clock(session)
    before = row.email_redirect_override
    row.email_redirect_override = cleaned
    row.updated_by = actor
    row.updated_at = utcnow()
    session.add(row)

    session.add(
        AuditLog(
            invoice_id=None,
            actor=actor,
            action=AuditAction.DEMO_EMAIL_REDIRECTED,
            detail={
                "from": before,
                "to": cleaned,
                "effective": cleaned or settings.email_redirect_to,
                "note": "Reminder mail destination changed. Redirection itself cannot "
                "be disabled here — clearing falls back to EMAIL_REDIRECT_TO.",
            },
        )
    )
    session.commit()
    session.refresh(row)

    runtime.set_email_redirect_override(cleaned)
    log.info("demo_email.redirected", to=cleaned, actor=actor)
    return row


def advance(session: Session, *, days: int, actor: str) -> DemoSettings:
    """Move the simulated clock forward and record who did it."""
    _require_enabled()
    if days < 1 or days > MAX_ADVANCE_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_ADVANCE_DAYS}")

    row = get_clock(session)
    before = row.offset_days
    row.offset_days = before + days
    row.updated_by = actor
    row.updated_at = utcnow()
    if row.started_at is None:
        row.started_at = utcnow()
    session.add(row)

    session.add(
        AuditLog(
            invoice_id=None,
            actor=actor,
            action=AuditAction.DEMO_CLOCK_ADVANCED,
            detail={
                "days": days,
                "offset_before": before,
                "offset_after": row.offset_days,
                "note": "Simulated clock moved forward. Business logic is unchanged; "
                "only the date the system believes it is has moved.",
            },
        )
    )
    session.commit()
    session.refresh(row)

    clock.set_runtime_offset(row.offset_days)
    log.info("demo_clock.advanced", days=days, offset=row.offset_days, actor=actor)
    return row


def reset(session: Session, *, actor: str) -> DemoSettings:
    """Return the clock to real time."""
    _require_enabled()
    row = get_clock(session)
    before = row.offset_days
    row.offset_days = 0
    row.updated_by = actor
    row.updated_at = utcnow()
    row.started_at = None
    session.add(row)

    session.add(
        AuditLog(
            invoice_id=None,
            actor=actor,
            action=AuditAction.DEMO_CLOCK_RESET,
            detail={"offset_before": before, "offset_after": 0},
        )
    )
    session.commit()
    session.refresh(row)

    clock.set_runtime_offset(0)
    log.info("demo_clock.reset", offset_before=before, actor=actor)
    return row
