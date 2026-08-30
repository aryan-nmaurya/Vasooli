"""Demo clock endpoints. Demo controls.

The cadence fires at 3, 10 and 21 days overdue. Nobody reviewing this project will
wait three weeks, so these move a simulated clock forward and run the ordinary
recovery cycle against the later date.

Nothing here fabricates activity. `/advance` shifts the clock and calls the same
`run_recovery_cycle` the scheduler calls; whether a reminder goes out is still the
policy engine's decision, made against the same rules. What changes is the date the
system believes it is — which is exactly what a reviewer needs to compress and
nothing more.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import Operator, OperatorRequired
from app.core.clock import now_ist, real_now_ist
from app.core.config import settings
from app.core.db import SessionDep
from app.core.runtime import effective_email_redirect, email_redirect_override
from app.models import AuditActor
from app.services.demo_control import (
    MAX_ADVANCE_DAYS,
    DemoControlsDisabledError,
    advance,
    get_clock,
    reset,
    set_email_redirect,
)
from app.services.recovery import run_recovery_cycle

router = APIRouter(prefix="/api/demo", tags=["demo"], dependencies=[OperatorRequired])


class AdvanceRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=MAX_ADVANCE_DAYS)
    #: Run the recovery cycle after moving the clock. On by default — advancing time
    #: and then not acting on it shows a reviewer nothing.
    run_cycle: bool = True
    #: Evaluate without sending. Lets a reviewer watch the policy decide before any
    #: mail leaves.
    dry_run: bool = False


class RedirectRequest(BaseModel):
    #: Null or empty clears the override and falls back to EMAIL_REDIRECT_TO. There is
    #: no value here that turns redirection off.
    address: str | None = Field(default=None, max_length=254)


class ClockState(BaseModel):
    enabled: bool
    offset_days: int
    simulated_date: str
    real_date: str
    updated_by: str | None = None
    max_advance_days: int = MAX_ADVANCE_DAYS
    #: Where reminder mail is actually going right now, and whether that is a runtime
    #: override or the deployment's own default.
    email_to: str | None = None
    email_is_override: bool = False


def _state(session) -> ClockState:
    row = get_clock(session) if settings.demo_controls_enabled else None
    real = real_now_ist()
    return ClockState(
        enabled=settings.demo_controls_enabled,
        offset_days=row.offset_days if row else 0,
        simulated_date=now_ist().strftime("%d %b %Y, %H:%M"),
        real_date=real.strftime("%d %b %Y, %H:%M"),
        updated_by=row.updated_by if row else None,
        email_to=effective_email_redirect(),
        email_is_override=bool(email_redirect_override()),
    )


@router.get("/clock", response_model=ClockState)
def clock_state(session: SessionDep) -> ClockState:
    """Where the simulated clock currently is."""
    return _state(session)


@router.post("/advance")
def advance_clock(payload: AdvanceRequest, session: SessionDep, operator: Operator) -> dict:
    """Move the clock forward, then let the recovery cycle react to the new date."""
    try:
        advance(session, days=payload.days, actor=AuditActor.human(operator))
    except DemoControlsDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    report = None
    if payload.run_cycle:
        report = run_recovery_cycle(session, dry_run=payload.dry_run).as_dict()

    return {"clock": _state(session).model_dump(), "cycle": report}


@router.post("/reset")
def reset_clock(session: SessionDep, operator: Operator) -> dict:
    """Return to real time. Does not touch the ledger."""
    try:
        reset(session, actor=AuditActor.human(operator))
    except DemoControlsDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"clock": _state(session).model_dump()}


@router.post("/email-redirect")
def set_redirect(payload: RedirectRequest, session: SessionDep, operator: Operator) -> dict:
    """Point reminder mail at a reviewer's own inbox.

    The one thing a reviewer cannot otherwise do without deployment access: receive a
    real reminder and reply to it, which is the only way to exercise the inbound path
    end to end. Clearing falls back to the deployment default — this cannot switch
    redirection off and start mailing the seeded ledger's invented addresses.
    """
    try:
        set_email_redirect(session, address=payload.address, actor=AuditActor.human(operator))
    except DemoControlsDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"clock": _state(session).model_dump()}
