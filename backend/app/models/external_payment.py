"""Money that reached the merchant without passing through a Vasooli payment link.

The gap this closes is the one the audit named as fatal: Vasooli could only ever see
payments made through Payment Links it created itself. A B2B customer who paid by NEFT,
by UPI outside the link, by cheque, or against a Razorpay object Vasooli never provisioned
was — from the system's point of view — still a defaulter, and kept receiving reminders.
Chasing someone who has already paid is the single worst thing a collections system can
do, and no amount of webhook correctness prevented it.

**This table is an operator's assertion, not verified payment truth**, and the
distinction is kept structurally rather than in a comment. `recorded_by` names the
person, `reference` records what they say identifies the transaction (a UTR, a cheque
number, a Razorpay payment id), and rows are never edited or deleted — a mistake is
corrected by reversing the row, which leaves both the claim and the correction in the
trail. Reconciliation's own totals stay in their own column, so nothing here can
overwrite what Razorpay reported.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Column, Date, Field, SQLModel

from app.models.base import fk_column, money_column, pk_column, timestamp_column


class PaymentMethod:
    """How the money arrived. Deliberately coarse.

    These are the categories a finance operator can answer confidently from a bank
    statement. A longer list would mostly produce guesses, and the value is used for
    reporting rather than for any decision, so a wrong fine-grained value would be
    worse than a right coarse one.
    """

    BANK_TRANSFER = "bank_transfer"  # NEFT / RTGS / IMPS
    UPI = "upi"
    CHEQUE = "cheque"
    CASH = "cash"
    #: A Razorpay payment that could not be tied to one of our links — the settlement
    #: an operator resolved by hand out of the unmatched queue.
    RAZORPAY_UNLINKED = "razorpay_unlinked"
    #: A credit note, discount, or write-down agreed with the customer. Not money in
    #: the bank, but it does reduce what is owed, and chasing the difference is the
    #: same mistake as chasing a paid invoice.
    ADJUSTMENT = "adjustment"

    ALL = frozenset({BANK_TRANSFER, UPI, CHEQUE, CASH, RAZORPAY_UNLINKED, ADJUSTMENT})


class ExternalPayment(SQLModel, table=True):
    __tablename__ = "external_payments"
    __table_args__ = (
        # The same bank transfer can legitimately settle two different invoices, so
        # the reference is unique per invoice rather than globally. Within one invoice
        # it is the duplicate guard: an operator entering the same UTR twice — the
        # ordinary way a payment gets double-counted — is refused by the database
        # rather than by a check someone has to remember to write.
        UniqueConstraint("invoice_id", "reference", name="uq_external_payments_invoice_reference"),
        CheckConstraint("amount_paise > 0", name="ck_external_payments_amount_positive"),
    )

    id: uuid.UUID = Field(sa_column=pk_column())
    invoice_id: uuid.UUID = Field(sa_column=fk_column("invoices.id"))

    amount_paise: int = Field(sa_column=money_column())
    method: str
    #: What the operator says identifies this transaction in the bank statement.
    #: Required, including for cash — "cash, no reference" is not a reference, and an
    #: entry nobody can trace back is an entry nobody can check.
    reference: str = Field(index=True)
    #: The date on the bank statement, which is not the date it was typed in.
    received_on: date = Field(sa_column=Column(Date, nullable=False))
    note: str = ""

    recorded_by: str
    recorded_at: datetime = Field(sa_column=timestamp_column(default_now=True, index=True))

    # --- Correction ----------------------------------------------------------
    #: Reversal rather than deletion. The row stays, both the claim and its retraction
    #: are visible, and the invoice balance is recomputed from what is left standing.
    reversed_at: datetime | None = Field(sa_column=timestamp_column(nullable=True))
    reversed_by: str | None = None
    reversal_reason: str | None = None

    @property
    def is_active(self) -> bool:
        """Still counting towards the invoice balance."""
        return self.reversed_at is None
