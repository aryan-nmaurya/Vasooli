"""Evaluation output. Doc §9."""

import csv
import pathlib

from app.core.constants import MAX_AUTOMATED_REMINDERS
from app.core.money import format_inr
from eval.metrics import EvalResult

OUT_DIR = pathlib.Path(__file__).resolve().parent / "out"


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "—"


def print_main(result: EvalResult, *, days: int) -> None:
    """The Doc §9 table."""
    print("\nVASOOLI EVALUATION")
    print("=" * 46)
    rows = [
        ("Test invoices", str(result.invoices)),
        ("Simulated days", str(days)),
        ("Total overdue", format_inr(result.total_overdue_paise + result.recovered_paise)),
        ("Recovered (within window)", format_inr(result.recovered_paise)),
        ("Recovery rate", _pct(result.recovery_rate)),
        (
            "Avg. days to recovery",
            f"{result.avg_days_to_recovery:.1f}" if result.avg_days_to_recovery else "—",
        ),
        ("Automation rate", _pct(result.automation_rate)),
        ("", ""),
        ("Contacts sent", str(result.total_contacts)),
        ("Contacts per invoice", f"{result.contacts_per_invoice:.2f}"),
        ("Escalated to human", str(result.escalated)),
        ("False escalations", str(result.false_escalations)),
        ("Missed escalations", str(result.missed_escalations)),
        ("", ""),
        ("Promises logged", str(result.promises_logged)),
        ("  kept", str(result.promises_kept)),
        ("  broken", str(result.promises_broken)),
        ("", ""),
        (
            "Diagnosis vs static label",
            f"{_pct(result.diagnosis_accuracy)} "
            f"({result.diagnosis_correct}/{result.diagnosis_total})",
        ),
        (
            "  + correct reclassification",
            f"{_pct(result.diagnosis_defensible)} ({result.diagnosis_reclassified} became"
            " unresponsive)",
        ),
    ]
    for label, value in rows:
        print(f"{label:<30}{value:>16}" if label else "")

    if result.diagnosis_reclassified:
        print(
            f"\n  {result.diagnosis_reclassified} invoices were labelled cash-constrained at"
            "\n  generation but stopped replying after Tier 2. Doc §3 defines that as"
            "\n  'unresponsive', so the classifier is right and the fixed label is stale."
        )

    if result.confusion:
        print("\nGenuine misclassifications")
        for pair, count in result.confusion.most_common():
            print(f"  {count:>4}  {pair}")

    if result.policy_rejections:
        print("\nPolicy rejections by rule")
        for rule, count in result.policy_rejections.most_common():
            print(f"  {count:>4}  {rule}")


def print_violations(result: EvalResult) -> bool:
    """Compliance breaches. Returns True when the run is clean."""
    v = result.violations
    print("\nCOMPLIANCE CHECKS")
    print("-" * 46)
    checks = [
        (f"No invoice exceeded {MAX_AUTOMATED_REMINDERS} reminders", v.over_cap),
        ("No disputed invoice was chased", v.disputed_contacted),
        ("No same-week repeat contact", v.cooldown_breached),
        ("Nobody chased after paying", v.contacted_after_payment),
    ]
    for label, breaches in checks:
        mark = "✓" if not breaches else "✗"
        print(f"  {mark} {label}" + (f"  ({len(breaches)} breaches)" if breaches else ""))
        for breach in breaches[:5]:
            print(f"      {breach}")
    return v.total == 0


def print_comparison(results: list[EvalResult]) -> None:
    """Baselines side by side.

    A recovery rate on its own is unreadable — some of those invoices would have been
    paid whatever anyone did. The no-chasing column is what the ledger collects by
    itself, and the naive column is what "just send reminders" costs in customer
    contact to achieve roughly the same thing.
    """
    print("\nBASELINE COMPARISON")
    print("=" * 74)
    header = f"{'':<26}" + "".join(f"{r.policy:>16}" for r in results)
    print(header)
    print("-" * 74)

    def row(label: str, fn) -> None:
        print(f"{label:<26}" + "".join(f"{fn(r):>16}" for r in results))

    row("Recovered", lambda r: format_inr(r.recovered_paise))
    row("Recovery rate", lambda r: _pct(r.recovery_rate))
    row(
        "Avg days to recovery",
        lambda r: f"{r.avg_days_to_recovery:.1f}" if r.avg_days_to_recovery else "—",
    )
    row("Contacts sent", lambda r: str(r.total_contacts))
    row("Contacts per invoice", lambda r: f"{r.contacts_per_invoice:.2f}")
    row("Escalated to human", lambda r: str(r.escalated))
    row("Compliance breaches", lambda r: str(r.violations.total))


def write_csv(results: list[EvalResult]) -> pathlib.Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "results.csv"
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "policy",
                "invoices",
                "recovered_paise",
                "recovery_rate",
                "avg_days_to_recovery",
                "automation_rate",
                "total_contacts",
                "contacts_per_invoice",
                "escalated",
                "false_escalations",
                "missed_escalations",
                "promises_logged",
                "promises_kept",
                "promises_broken",
                "diagnosis_accuracy",
                "compliance_breaches",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.policy,
                    r.invoices,
                    r.recovered_paise,
                    round(r.recovery_rate, 4),
                    round(r.avg_days_to_recovery, 2) if r.avg_days_to_recovery else "",
                    round(r.automation_rate, 4) if r.automation_rate else "",
                    r.total_contacts,
                    round(r.contacts_per_invoice, 2),
                    r.escalated,
                    r.false_escalations,
                    r.missed_escalations,
                    r.promises_logged,
                    r.promises_kept,
                    r.promises_broken,
                    round(r.diagnosis_accuracy, 4),
                    r.violations.total,
                ]
            )
    return path
