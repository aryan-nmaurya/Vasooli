"""SQLModel entities. Doc §8.

Every model MUST be imported here. Alembic autogenerate only sees tables registered on
SQLModel.metadata at import time and will silently emit an empty migration for
anything it misses — a failure mode that looks like "the migration worked" right up
until the first query.
"""

from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.customer import Customer
from app.models.dispute_case import DisputeCase
from app.models.invoice import Invoice
from app.models.merchant import Merchant
from app.models.payment_link import PaymentLink, PaymentLinkStatus
from app.models.promise import Promise
from app.models.reconciliation_event import ReconciliationEvent
from app.models.reminder import Reminder

__all__ = [
    "AuditAction",
    "AuditActor",
    "AuditLog",
    "Customer",
    "DisputeCase",
    "Invoice",
    "Merchant",
    "PaymentLink",
    "PaymentLinkStatus",
    "Promise",
    "ReconciliationEvent",
    "Reminder",
]
