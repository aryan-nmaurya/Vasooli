"""External dead-man heartbeat contract, and the cross-process scheduler verdict."""

from datetime import timedelta

from app.core.clock import utcnow as _utcnow
from app.core.config import settings
from app.models.job_run import JobRun, JobStatus
from app.scheduler import jobs
from app.services.automation import _verdict


class _Response:
    def __init__(self) -> None:
        self.checked = False

    def raise_for_status(self) -> None:
        self.checked = True


def test_heartbeat_uses_a_bounded_request_and_checks_status(monkeypatch):
    response = _Response()
    seen: dict[str, object] = {}

    def fake_get(url: str, *, timeout: float):
        seen.update(url=url, timeout=timeout)
        return response

    monkeypatch.setattr(jobs.httpx, "get", fake_get)
    jobs._heartbeat("https://monitor.invalid/secret", check="service")

    assert seen == {"url": "https://monitor.invalid/secret", "timeout": 5.0}
    assert response.checked is True


def test_empty_heartbeat_url_does_not_make_a_request(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("network request should not be attempted")

    monkeypatch.setattr(jobs.httpx, "get", unexpected)
    jobs._heartbeat("", check="service")


# --- Scheduler verdict across processes ------------------------------------------
#
# In production the API container runs with SCHEDULER_ENABLED=false by design: the
# scheduler lives in its own container against the same database. The verdict used to
# check that flag before looking at job history, so the dashboard told a judge the
# automation was dead while the job table showed a run succeeding 408ms earlier.


def _run(*, status=JobStatus.SUCCEEDED, ago_seconds=5) -> JobRun:
    moment = _utcnow() - timedelta(seconds=ago_seconds)
    return JobRun(job_id="recovery_cycle", status=status, started_at=moment, finished_at=moment)


def test_recent_run_beats_the_local_scheduler_flag(monkeypatch):
    """A job running in the scheduler container is not 'disabled' in the API container."""
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    run = _run()
    state, explanation = _verdict("recovery_cycle", run, run)
    assert state == "healthy", explanation


def test_no_history_and_no_scheduler_is_still_disabled(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    state, _ = _verdict("recovery_cycle", None, None)
    assert state == "disabled"


def test_no_history_with_scheduler_on_is_unknown_not_broken(monkeypatch):
    """A fresh deploy has no history; calling that 'failing' is a false alarm."""
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    state, _ = _verdict("recovery_cycle", None, None)
    assert state == "unknown"


def test_a_failed_latest_run_still_reports_failing(monkeypatch):
    """The fix must not let the history path mask a genuine failure."""
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    success = _run(ago_seconds=600)
    failure = _run(status=JobStatus.FAILED, ago_seconds=5)
    state, _ = _verdict("recovery_cycle", success, failure)
    assert state == "failing"
