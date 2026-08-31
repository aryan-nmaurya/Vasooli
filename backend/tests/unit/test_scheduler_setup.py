from app.core.config import settings
from app.scheduler import setup


class FakeScheduler:
    def __init__(self, **_kwargs):
        self.jobs = []
        self.started = False

    def add_job(self, _handler, _trigger, **kwargs):
        self.jobs.append(kwargs)

    def start(self):
        self.started = True

    def get_jobs(self):
        return [type("Job", (), {"id": job["id"]})() for job in self.jobs]


def test_scheduler_registers_automatic_erp_polling(monkeypatch):
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "process_role", "scheduler")
    monkeypatch.setattr(setup, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(setup, "_scheduler", None)

    scheduler = setup.start_scheduler()

    assert scheduler is not None
    assert scheduler.started is True
    assert "erp_sync" in {job["id"] for job in scheduler.jobs}
