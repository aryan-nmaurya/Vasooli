"""SQLModel entities. Doc §8.

Every model MUST be imported here. Alembic autogenerate only sees tables registered on
SQLModel.metadata at import time and will silently emit an empty migration for
anything it misses — a failure mode that looks like "the migration worked" right up
until the first query.
"""

from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.billing import (
    BillingCustomer,
    BillingEntitlement,
    BillingEvent,
    BillingInvoice,
    BillingPaymentAttempt,
    BillingPlan,
    BillingRefund,
    BillingSubscription,
)
from app.models.controls import (
    MerchantUsageBucket,
    ReminderPolicyVersion,
    SendingDomain,
    SuppressionEntry,
)
from app.models.customer import Customer
from app.models.demo_settings import DemoSettings
from app.models.dispute_case import DisputeCase
from app.models.email_event import DeliveryState, EmailEvent
from app.models.external_payment import ExternalPayment, PaymentMethod
from app.models.iam import (
    AuditEvent,
    AuthEvent,
    AuthToken,
    MerchantInvitation,
    MerchantMembership,
    MFAFactor,
    Permission,
    Role,
    RolePermission,
    UserPermissionOverride,
    UserSession,
)
from app.models.inbound_message import InboundMessage
from app.models.integrations import ErpConnection, ErpRecord, ErpSyncRun, IntegrationFailure
from app.models.invoice import Invoice
from app.models.job_run import JobRun, JobStatus
from app.models.merchant import Merchant
from app.models.operations import DataRequest
from app.models.operator_account import OperatorAccount
from app.models.payment_connection import PaymentConnection
from app.models.payment_link import PaymentLink, PaymentLinkStatus
from app.models.promise import Promise
from app.models.reconciliation_event import ReconciliationEvent
from app.models.reminder import Reminder
from app.models.user import User, UserStatus

__all__ = [
    "AuditAction",
    "AuditActor",
    "AuditEvent",
    "AuditLog",
    "BillingCustomer",
    "BillingEntitlement",
    "BillingEvent",
    "BillingInvoice",
    "BillingPaymentAttempt",
    "BillingPlan",
    "BillingRefund",
    "BillingSubscription",
    "AuthEvent",
    "AuthToken",
    "Customer",
    "DemoSettings",
    "DeliveryState",
    "DisputeCase",
    "EmailEvent",
    "ExternalPayment",
    "Invoice",
    "ErpConnection",
    "ErpRecord",
    "ErpSyncRun",
    "IntegrationFailure",
    "InboundMessage",
    "JobRun",
    "JobStatus",
    "Merchant",
    "DataRequest",
    "MerchantInvitation",
    "MerchantMembership",
    "MFAFactor",
    "OperatorAccount",
    "PaymentLink",
    "PaymentMethod",
    "PaymentLinkStatus",
    "PaymentConnection",
    "ReminderPolicyVersion",
    "SuppressionEntry",
    "SendingDomain",
    "MerchantUsageBucket",
    "Promise",
    "Permission",
    "ReconciliationEvent",
    "Reminder",
    "Role",
    "RolePermission",
    "UserPermissionOverride",
    "User",
    "UserSession",
    "UserStatus",
]
