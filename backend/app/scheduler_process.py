"""Standalone scheduler process used by the production compose stack."""

from __future__ import annotations

import time

from app.core.config import settings
from app.scheduler.setup import shutdown_scheduler, start_scheduler


def run() -> None:
    settings.scheduler_enabled = True
    settings.process_role = "scheduler"
    start_scheduler()
    try:
        while True:
            time.sleep(30)
    finally:
        shutdown_scheduler()


if __name__ == "__main__":
    run()
