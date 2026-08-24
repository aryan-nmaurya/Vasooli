"""Evaluation metrics. Doc §9.

Recovery figures come from app.services.metrics — the same function the dashboard
calls. Reimplementing them here would let the report and the dashboard disagree, and
the moment to discover that is not on stage.

Everything below is what only the evaluation can know: how each simulated customer
would have behaved, and therefore whether a decision was right.
"""

from collections import Counter
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.core.constants import MAX_AUTOMATED_REMINDERS, MIN_COOLDOWN_DAYS, InvoiceStatus
from app.models import AuditAction, AuditLog, Invoice, Promise, Reminder
from app.services.metrics import compute_metrics


@dataclass
class PolicyViolations:
    """Compliance breaches. These are failures, not report lines.

    Every one of these is a promise the spec makes about how customers are treated. A
    run that produces any of them has not merely scored badly — it has behaved in a
    way the project claims is impossible.
    """

    over_cap: list[str] = field(default_factory=list)
    disputed_contacted: list[str] = field(default_factory=list)
    cooldown_breached: list[str] = field(default_factory=list)
    contacted_after_payment: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.over_cap)
            + len(self.disputed_contacted)
            + len(self.cooldown_breached)
            + len(self.contacted_after_payment)
        )


@dataclass
class EvalResult:
    policy: str
    invoices: int
    total_overdue_paise: int
    recovered_paise: int
    recovery_rate: float
    avg_days_to_recovery: float | None
    automation_rate: float | None
    total_contacts: int
    contacts_per_invoice: float
    escalated: int
    false_escalations: int
    missed_escalations: int
    promises_logged: int
    promises_kept: int
    promises_broken: int
    diagnosis_correct: int
    diagnosis_total: int
    #: Correctly became 'unresponsive' after Tier 2 with no reply — a rule doing
    #: its job, not a misdiagnosis.
    diagnosis_reclassified: int
    confusion: Counter
    policy_rejections: Counter
    violations: PolicyViolations
    #: Reminders that reached the customer, over reminders attempted. Separates a
    #: policy that chose not to send from one that tried and failed.
    delivery_success_rate: float | None = None
    #: Invoices where the cadence ran out with nothing recovered — the cost of a
    #: bounded policy, stated rather than hidden.
    exhausted_unrecovered: int = 0
    #: Contacts per rupee recovered. The efficiency figure the baseline table is
    #: really about.
    contacts_per_lakh_recovered: float | None = None

    @property
    def diagnosis_accuracy(self) -> float:
        return self.diagnosis_correct / self.diagnosis_total if self.diagnosis_total else 0.0

    @property
    def diagnosis_defensible(self) -> float:
        """Accuracy counting cadence-driven reclassification as correct."""
        if not self.diagnosis_total:
            return 0.0
        return (self.diagnosis_correct + self.diagnosis_reclassified) / self.diagnosis_total


def check_violations(session: Session, ground_truth: dict[str, dict[str, str]]) -> PolicyViolations:
    """Assert the compliance guarantees held across the whole run."""
    violations = PolicyViolations()

    reminders_by_invoice: dict[str, list[Reminder]] = {}
    for reminder in session.exec(select(Reminder)).all():
        reminders_by_invoice.setdefault(str(reminder.invoice_id), []).append(reminder)

    for invoice in session.exec(select(Invoice)).all():
        sent = sorted(
            reminders_by_invoice.get(str(invoice.id), []),
            key=lambda r: r.sent_at or r.created_at,
        )

        if len(sent) > MAX_AUTOMATED_REMINDERS:
            violations.over_cap.append(f"{invoice.invoice_number}: {len(sent)} reminders")

        truth = ground_truth.get(invoice.invoice_number, {})
        if truth.get("reason") == "dispute_likely" and sent:
            violations.disputed_contacted.append(
                f"{invoice.invoice_number}: {len(sent)} reminders on a disputed invoice"
            )

        for earlier, later in zip(sent, sent[1:], strict=False):
            a, b = earlier.sent_at, later.sent_at
            if a and b and (b - a).days < MIN_COOLDOWN_DAYS:
                violations.cooldown_breached.append(
                    f"{invoice.invoice_number}: {(b - a).days}d between tiers "
                    f"{earlier.tier} and {later.tier}"
                )

        if invoice.recovered_at:
            for reminder in sent:
                if reminder.sent_at and reminder.sent_at > invoice.recovered_at:
                    violations.contacted_after_payment.append(
                        f"{invoice.invoice_number}: chased after payment"
                    )

    return violations


def evaluate(
    session: Session,
    ground_truth: dict[str, dict[str, str]],
    *,
    policy: str,
    contacts_override: int | None = None,
) -> EvalResult:
    base = compute_metrics(session)
    invoices = list(session.exec(select(Invoice)).all())
    reminders = list(session.exec(select(Reminder)).all())
    promises = list(session.exec(select(Promise)).all())

    # Escalation quality. Doc §9.
    #
    # A false escalation is a human pulled in for a customer who would have paid on
    # their own or after a single nudge — the cost of over-caution. A missed escalation
    # is a genuine defaulter or dispute left in an automated loop that will never
    # resolve it. Both are counted against the policy.
    false_escalations = 0
    missed_escalations = 0
    for invoice in invoices:
        truth = ground_truth.get(invoice.invoice_number)
        if truth is None:
            continue
        escalated = invoice.escalated_to_human_at is not None
        easy = truth["outcome"] in {"would_pay_anyway", "needs_one_nudge"}
        needs_human = truth["outcome"] == "would_default" or truth["reason"] == "dispute_likely"

        if escalated and easy and invoice.status == InvoiceStatus.RECOVERED:
            false_escalations += 1
        if needs_human and not escalated:
            missed_escalations += 1

    # Diagnosis accuracy, with one honest adjustment.
    #
    # The ground-truth label is fixed at generation time, but the category is not
    # static: Doc §3 defines "unresponsive" as no reply after the Tier 2 reminder. A
    # cash-constrained customer who never answers genuinely BECOMES unresponsive, and
    # the classifier saying so is right, not wrong. Counting that as an error would
    # understate accuracy by pretending a correct rule is a mistake — so it is
    # reported separately rather than quietly folded into either number.
    confusion: Counter = Counter()
    correct = 0
    diagnosed = 0
    reclassified = 0
    for invoice in invoices:
        truth = ground_truth.get(invoice.invoice_number)
        if truth is None or invoice.reason_category is None:
            continue
        diagnosed += 1
        predicted = str(invoice.reason_category)
        if predicted == truth["reason"]:
            correct += 1
            continue
        became_unresponsive = (
            predicted == "unresponsive"
            and invoice.current_tier >= 2
            and truth["reason"] != "dispute_likely"
        )
        if became_unresponsive:
            reclassified += 1
        else:
            confusion[f"{truth['reason']} → {predicted}"] += 1

    rejections: Counter = Counter()
    for entry in session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.POLICY_REJECTED)
    ).all():
        for check in (entry.detail or {}).get("checks", []):
            if not check.get("passed"):
                rejections[check["name"]] += 1

    return EvalResult(
        policy=policy,
        invoices=len(invoices),
        total_overdue_paise=base.total_overdue_paise,
        recovered_paise=base.recovered_paise,
        recovery_rate=base.recovery_rate,
        avg_days_to_recovery=base.avg_days_to_recovery,
        automation_rate=base.automation_rate,
        total_contacts=contacts if (contacts := contacts_override) is not None else len(reminders),
        contacts_per_invoice=(
            (contacts_override if contacts_override is not None else len(reminders)) / len(invoices)
            if invoices
            else 0.0
        ),
        escalated=sum(1 for i in invoices if i.escalated_to_human_at),
        false_escalations=false_escalations,
        missed_escalations=missed_escalations,
        promises_logged=len(promises),
        promises_kept=sum(1 for p in promises if str(p.status) == "kept"),
        promises_broken=sum(1 for p in promises if str(p.status) == "broken"),
        diagnosis_correct=correct,
        diagnosis_total=diagnosed,
        diagnosis_reclassified=reclassified,
        confusion=confusion,
        policy_rejections=rejections,
        violations=check_violations(session, ground_truth),
        delivery_success_rate=(
            sum(1 for r in reminders if r.sent_at) / len(reminders) if reminders else None
        ),
        exhausted_unrecovered=sum(
            1
            for i in invoices
            if i.reminders_sent >= MAX_AUTOMATED_REMINDERS and i.status != InvoiceStatus.RECOVERED
        ),
        contacts_per_lakh_recovered=(
            # Uses the override, not len(reminders): the naive baseline's contacts are
            # counted in memory because the schema refuses to store a fourth reminder,
            # so reading the table would report zero and make the worst policy look
            # like the most efficient one.
            (contacts_override if contacts_override is not None else len(reminders))
            / (base.recovered_paise / 1_00_000_00)
            if base.recovered_paise
            else None
        ),
    )
