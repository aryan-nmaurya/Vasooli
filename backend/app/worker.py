"""Durable background worker entrypoint for separated deployments.

The scheduler decides *when* work is due; this process performs retry, connector,
recovery and billing jobs without embedding APScheduler in an API replica. PostgreSQL
advisory/row locks make starting more than one worker safe.
"""

from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.scheduler.jobs import (
    billing_reconciliation_job,
    payment_link_sync_job,
    recovery_cycle_job,
    retry_operations_job,
)


def run() -> None:
    logging.basicConfig(level=settings.log_level)
    handlers = {
        "all": (
            retry_operations_job,
            payment_link_sync_job,
            recovery_cycle_job,
            billing_reconciliation_job,
        ),
        "recovery": (recovery_cycle_job,),
        "email": (retry_operations_job,),
        "erp": (payment_link_sync_job,),
        "billing": (billing_reconciliation_job,),
    }[settings.worker_kind]
    while True:
        for handler in handlers:
            handler()
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    run()
