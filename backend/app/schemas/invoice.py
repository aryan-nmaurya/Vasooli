"""Ingestion and read DTOs for invoices.

The important property here is what these models DON'T carry. The synthetic CSVs ship
with `ground_truth_reason` and `ground_truth_outcome` columns for the Phase 11 eval,
and those labels must never reach the database or a prompt — a classifier scored
against a label it was shown is measuring nothing.

`extra="ignore"` makes that structural rather than procedural: a row carrying label
columns can be handed straight to `InvoiceIngestRow` and the labels are dropped at the
parse boundary. The eval harness reads them from the CSV itself and keeps them in
memory, never in a row.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.constants import InvoiceStatus, ReasonCategory
from app.core.money import format_inr, rupees_to_paise


class InvoiceIngestRow(BaseModel):
    """One row of a receivables ledger, as it arrives from CSV or the batch API."""

    model_config = ConfigDict(extra="ignore")  # drops ground_truth_* and gen_* columns

    invoice_number: str = Field(min_length=1, max_length=64)

    customer_name: str = Field(min_length=1, max_length=200)
    customer_email: EmailStr
    #: Razorpay's Customers API expects a contact number; provisioning fails without it.
    customer_phone: str | None = None

    #: Decimal, never float. Pydantic parses "42000.50" exactly; a float would already
    #: have lost precision before app.core.money could reject it.
    amount_inr: Decimal = Field(gt=0)

    issued_at: date
    due_at: date
    terms_days: int = Field(default=30, ge=0, le=365)

    # --- Customer payment history. The only inputs diagnosis may use (Doc §3). ---
    customer_total_invoices: int = Field(default=0, ge=0)
    customer_invoices_paid_late: int = Field(default=0, ge=0)
    customer_invoices_defaulted: int = Field(default=0, ge=0)
    customer_broken_promises: int = Field(default=0, ge=0)
    customer_avg_invoice_inr: Decimal = Field(default=Decimal(0), ge=0)

    has_prior_dispute_note: bool = False

    @field_validator("has_prior_dispute_note", mode="before")
    @classmethod
    def _parse_csv_bool(cls, v: object) -> object:
        """CSV has no boolean type; accept the spellings a spreadsheet produces."""
        if isinstance(v, str):
            return v.strip().lower() in {"true", "1", "yes", "y"}
        return v

    @field_validator("customer_phone", mode="before")
    @classmethod
    def _blank_phone_is_none(cls, v: object) -> object:
        return v or None

    def model_post_init(self, _context: object) -> None:
        if self.due_at < self.issued_at:
            raise ValueError(
                f"{self.invoice_number}: due_at {self.due_at} precedes issued_at {self.issued_at}"
            )
        if self.customer_invoices_paid_late > self.customer_total_invoices:
            raise ValueError(
                f"{self.invoice_number}: invoices_paid_late "
                f"({self.customer_invoices_paid_late}) exceeds total "
                f"({self.customer_total_invoices})"
            )
        if self.customer_invoices_defaulted > self.customer_total_invoices:
            raise ValueError(
                f"{self.invoice_number}: invoices_defaulted "
                f"({self.customer_invoices_defaulted}) exceeds total "
                f"({self.customer_total_invoices})"
            )

    @property
    def amount_paise(self) -> int:
        return rupees_to_paise(self.amount_inr)

    @property
    def avg_invoice_paise(self) -> int:
        return rupees_to_paise(self.customer_avg_invoice_inr)


class BatchIngestRequest(BaseModel):
    merchant_id: uuid.UUID | None = None
    invoices: list[InvoiceIngestRow]
    #: Phase 3 wires this up. Accepted now so the contract does not change later.
    provision_virtual_accounts: bool = False
    #: Recompute due dates so the ledger lands on today's tier boundaries. Lets a CSV
    #: generated last week still produce a demo with invoices at day 3, 10, and 21.
    rebase_dates: bool = False


class IngestError(BaseModel):
    invoice_number: str
    error: str


class BatchIngestResponse(BaseModel):
    merchant_id: uuid.UUID
    ingested: int
    skipped_duplicates: int
    failed: int
    customers_created: int
    errors: list[IngestError] = Field(default_factory=list)


class InvoiceRead(BaseModel):
    """Read DTO. Money crosses the wire as integer paise plus a rendered string, so
    the frontend never does arithmetic on currency."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_number: str
    customer_name: str | None = None

    amount_paise: int
    amount_paid_paise: int
    outstanding_paise: int
    amount_display: str
    outstanding_display: str

    issued_at: datetime
    due_at: datetime
    days_overdue: int
    status: InvoiceStatus

    reason_category: ReasonCategory | None
    reason_explanation: str | None

    reminders_sent: int
    current_tier: int
    last_reminder_at: datetime | None
    escalated_to_human_at: datetime | None
    recovered_at: datetime | None

    @classmethod
    def from_invoice(cls, invoice, customer_name: str | None = None) -> "InvoiceRead":
        return cls(
            id=invoice.id,
            invoice_number=invoice.invoice_number,
            customer_name=customer_name,
            amount_paise=invoice.amount_paise,
            amount_paid_paise=invoice.amount_paid_paise,
            outstanding_paise=invoice.outstanding_paise,
            amount_display=format_inr(invoice.amount_paise),
            outstanding_display=format_inr(invoice.outstanding_paise),
            issued_at=invoice.issued_at,
            due_at=invoice.due_at,
            days_overdue=invoice.days_overdue,
            status=invoice.status,
            reason_category=invoice.reason_category,
            reason_explanation=invoice.reason_explanation,
            reminders_sent=invoice.reminders_sent,
            current_tier=invoice.current_tier,
            last_reminder_at=invoice.last_reminder_at,
            escalated_to_human_at=invoice.escalated_to_human_at,
            recovered_at=invoice.recovered_at,
        )
