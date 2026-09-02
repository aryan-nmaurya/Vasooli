"""What the runtime banner reports, measured rather than configured.

The banner is the one thing the reviewer guide points a judge at and calls honest,
so it is the last place that should be answering from configuration. Two of its
fields were doing exactly that:

- `ai` was `"enabled"` whenever an API key was present. During the audit it read
  `enabled` while 100% of AI calls were failing.
- `scheduler` reported `settings.scheduler_enabled` for the API container, which is
  `false` here by design because the scheduler runs in its own container. A judge
  read that as "automation is dead".

Both are now derived from recorded outcomes, which is what the rest of the system
already does for automation health.
"""

from sqlmodel import Session, select

from app.core.config import settings
from app.models import Reminder
from app.services.authorization import service_scope
from app.services.automation import automation_health

#: How many recent drafting outcomes to consider. Small enough to turn red quickly
#: while a run is still in progress, large enough that one transient 504 — which
#: failover already covers — does not flip the banner.
RECENT_AI_OUTCOMES = 8

#: `automation_health` verdicts, mapped to what the banner says about the system as
#: a whole rather than about the process serving this request.
_SCHEDULER_LABELS = {
    "healthy": "running",
    "stale": "stale",
    "failing": "failing",
    "disabled": "disabled",
    "unknown": "unknown",
}


def ai_health(session: Session) -> str:
    """`enabled`, `degraded`, or `disabled`, from what the models actually returned.

    Every reminder records the model that drafted it, or `template_fallback` when no
    model could answer, so the recent rows are evidence rather than intent. Read under
    `service_scope`: this is a platform health question, and the only column consulted
    is a model name — no tenant data crosses the boundary.
    """
    if not settings.google_api_key:
        return "disabled"
    with service_scope(session):
        recent = session.exec(
            select(Reminder.generated_by)
            .order_by(Reminder.created_at.desc())  # type: ignore[attr-defined]
            .limit(RECENT_AI_OUTCOMES)
        ).all()
    # No outcomes yet says nothing either way, so it is not reported as a failure.
    # Only an unbroken run of fallbacks is evidence that the models are not answering.
    if recent and all(source == "template_fallback" for source in recent):
        return "degraded"
    return "enabled"


def scheduler_health(session: Session) -> str:
    """What the scheduler is doing, read from `job_runs` rather than a flag.

    `automation_health` already computes the worst verdict across every job from the
    recorded runs, so this reuses it instead of growing a second opinion.
    """
    overall = automation_health(session).get("overall", "unknown")
    return _SCHEDULER_LABELS.get(overall, overall)
