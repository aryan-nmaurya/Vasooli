"""Day-by-day simulation against the real system. Doc §9.

What is real here: the policy engine, the recovery cycle, diagnosis, promise
extraction, the webhook handler, and reconciliation. All of it is production code
running against a real Postgres schema.

What is simulated: the passage of time, and the customers. Razorpay and email are
replaced at the integration boundary — the seam that exists precisely so this is
possible without a network.

That split matters. If the eval reimplemented the cadence rules, it would be measuring
a second implementation that could agree with the first while both were wrong.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.constants import InvoiceStatus
from app.integrations.razorpay_signature import compute_signature
from app.models import Invoice, PaymentLink, Reminder
from app.services.recovery import run_recovery_cycle
from app.services.replies import handle_reply
from eval.config import (
    BEHAVIOURS,
    NAIVE_INTERVAL_DAYS,
    PROMISE_HORIZON_DAYS,
    REPLY_TEMPLATES,
    Behaviour,
)


@dataclass
class InvoiceState:
    """What the simulator knows about one customer that the system does not."""

    invoice_id: str
    invoice_number: str
    ground_truth_reason: str
    ground_truth_outcome: str
    behaviour: Behaviour
    #: Day the money is due to land, once a trigger has fired.
    pays_on_day: int | None = None
    has_replied: bool = False
    promised_day: int | None = None
    promise_will_be_kept: bool = True
    paid: bool = False
    contacts_received: int = 0
    reminder_days: list[int] = field(default_factory=list)


class Simulator:
    def __init__(
        self,
        session: Session,
        client: TestClient,
        *,
        seed: int,
        policy: str = "vasooli",
        use_llm: bool = False,
    ) -> None:
        self.session = session
        self.client = client
        self.rng = random.Random(seed)
        self.policy = policy
        self.use_llm = use_llm
        self.states: dict[str, InvoiceState] = {}
        self.day = 0
        #: Naive-baseline contacts, counted here because the schema will not store them.
        self.naive_contacts = 0

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def load(self, ground_truth: dict[str, dict[str, str]]) -> None:
        """Attach hidden behaviour to each ingested invoice.

        The labels live only here, in memory. They were stripped at ingestion and are
        never written to a row, so nothing the system reads can see them.
        """
        for invoice in self.session.exec(select(Invoice)).all():
            truth = ground_truth.get(invoice.invoice_number)
            if truth is None:
                continue
            behaviour = BEHAVIOURS[truth["outcome"]]
            state = InvoiceState(
                invoice_id=str(invoice.id),
                invoice_number=invoice.invoice_number,
                ground_truth_reason=truth["reason"],
                ground_truth_outcome=truth["outcome"],
                behaviour=behaviour,
                promise_will_be_kept=self.rng.random() < behaviour.promise_kept_prob,
            )
            # Customers who would have paid regardless are already on their way.
            if behaviour.pays_after_tier == 0:
                lo, hi = behaviour.payment_delay_days
                state.pays_on_day = self.rng.randint(lo, hi)
            self.states[state.invoice_id] = state

    # ------------------------------------------------------------------
    # The day loop
    # ------------------------------------------------------------------

    def advance_to(self, day: int) -> None:
        """Move the whole system's clock. Everything reads through app.core.clock."""
        self.day = day
        object.__setattr__(settings, "demo_time_offset_days", day)

    def run_day(self, day: int) -> None:
        self.advance_to(day)

        if self.policy == "vasooli":
            run_recovery_cycle(self.session, use_llm=self.use_llm)
        elif self.policy == "naive":
            self._naive_cycle()
        # "none" sends nothing at all.

        if self.policy == "vasooli":
            self._observe_reminders()
        self._customers_react()
        self._settle_payments()

    def _observe_reminders(self) -> None:
        """Notice which invoices were contacted today, and schedule the consequences."""
        for reminder in self.session.exec(select(Reminder)).all():
            state = self.states.get(str(reminder.invoice_id))
            if state is None or reminder.tier in [t for t in state.reminder_days]:
                continue
            if len(state.reminder_days) >= reminder.tier:
                continue
            state.reminder_days.append(reminder.tier)
            state.contacts_received = len(state.reminder_days)

            trigger = state.behaviour.pays_after_tier
            if trigger and state.contacts_received >= trigger and state.pays_on_day is None:
                lo, hi = state.behaviour.payment_delay_days
                state.pays_on_day = self.day + self.rng.randint(lo, hi)

    def _customers_react(self) -> None:
        """Replies: a promise, a complaint, or nothing much."""
        for state in self.states.values():
            if state.paid or not state.contacts_received or state.has_replied:
                continue
            if self.rng.random() > state.behaviour.reply_prob:
                continue

            state.has_replied = True

            # A disputed invoice is disputed regardless of how it is chased.
            if state.ground_truth_reason == "dispute_likely":
                body = self.rng.choice(REPLY_TEMPLATES["complaint"])
            elif self.rng.random() < state.behaviour.promise_prob:
                offset = self.rng.randint(*PROMISE_HORIZON_DAYS)
                promised = date.today() + timedelta(days=offset)
                body = self.rng.choice(REPLY_TEMPLATES["promise"]).format(date=promised.isoformat())
                state.promised_day = self.day + offset
                if state.promise_will_be_kept and state.pays_on_day is None:
                    state.pays_on_day = state.promised_day
                elif not state.promise_will_be_kept:
                    # A broken promise: the date passes with nothing arriving.
                    state.pays_on_day = None
            else:
                body = self.rng.choice(REPLY_TEMPLATES["vague"])

            # The naive baseline has no reply handling at all — that is the point of it.
            if self.policy != "vasooli":
                continue

            invoice = self.session.get(Invoice, state.invoice_id)
            if invoice is not None:
                handle_reply(self.session, invoice, body, use_llm=self.use_llm)

    def _settle_payments(self) -> None:
        """Fire a signed webhook for anyone paying today, through the real endpoint."""
        for state in self.states.values():
            if state.paid or state.pays_on_day is None or state.pays_on_day > self.day:
                continue

            invoice = self.session.get(Invoice, state.invoice_id)
            if invoice is None or invoice.status == InvoiceStatus.WRITTEN_OFF:
                continue

            link = self.session.exec(
                select(PaymentLink).where(PaymentLink.invoice_id == invoice.id)
            ).first()
            if link is None:
                continue

            self._post_payment(invoice, link)
            state.paid = True

    def _post_payment(self, invoice: Invoice, link: PaymentLink) -> None:
        payload = {
            "entity": "event",
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": link.razorpay_payment_link_id,
                        "reference_id": link.reference_id,
                        "amount": invoice.amount_paise,
                        "amount_paid": invoice.amount_paise,
                        "status": "paid",
                        "notes": {"invoice_id": str(invoice.id)},
                    }
                },
                "payment": {"entity": {"id": f"pay_{invoice.invoice_number}"}},
            },
        }
        raw = json.dumps(payload).encode()
        self.client.post(
            "/api/webhooks/razorpay",
            content=raw,
            headers={
                "X-Razorpay-Signature": compute_signature(raw, settings.razorpay_webhook_secret),
                "X-Razorpay-Event-Id": f"evt_{invoice.invoice_number}_{self.day}",
                "Content-Type": "application/json",
            },
        )
        self.session.expire_all()

    # ------------------------------------------------------------------
    # Baseline policy
    # ------------------------------------------------------------------

    def _naive_cycle(self) -> None:
        """Contact every customer every few days, forever, with no rules.

        No caps, no cooldown, no dispute routing, no promise pausing. This is what
        "just send reminders" actually looks like.

        Contacts are tallied in memory rather than written as Reminder rows, because
        THEY CANNOT BE WRITTEN: `invoices.reminders_sent` carries a CHECK constraint of
        3, and `reminders.tier` is constrained to 1-3. The schema physically refuses to
        record a fourth automated contact against an invoice. That is worth stating
        plainly — the cap is not a setting this baseline could have turned off.
        """
        from app.core.clock import days_overdue as days_overdue_for

        for invoice in self.session.exec(select(Invoice)).all():
            if invoice.status in (InvoiceStatus.RECOVERED, InvoiceStatus.WRITTEN_OFF):
                continue
            overdue = days_overdue_for(invoice.due_at)
            if overdue < 3 or overdue % NAIVE_INTERVAL_DAYS != 0:
                continue

            state = self.states.get(str(invoice.id))
            if state is None:
                continue
            state.contacts_received += 1
            self.naive_contacts += 1

            # A naive chaser has no idea what tier it is on; it just keeps sending.
            trigger = state.behaviour.pays_after_tier
            if trigger and state.contacts_received >= trigger and state.pays_on_day is None:
                lo, hi = state.behaviour.payment_delay_days
                state.pays_on_day = self.day + self.rng.randint(lo, hi)
