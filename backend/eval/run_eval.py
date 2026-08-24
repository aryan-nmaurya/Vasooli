"""Run the evaluation. Doc §9.

    uv run python -m eval.run_eval
    uv run python -m eval.run_eval --compare-baselines
    uv run python -m eval.run_eval --days 45 --seed 42 --live-llm --limit 20

Runs against its own database so it can never disturb the demo ledger or the test
database. The LLM is mocked by default: the deterministic paths are what must hold
under quota exhaustion, and a 150-invoice, 45-day run against a live model would be
slow, costly, and non-reproducible.
"""

import argparse
import csv
import os
import pathlib
import sys
import time

# Must be set before anything under app.* is imported — settings are read at import.
EVAL_DB = os.environ.get("VASOOLI_EVAL_DATABASE_URL", "postgresql://localhost:5432/vasooli_eval")
os.environ["DATABASE_URL"] = EVAL_DB
os.environ["ENVIRONMENT"] = "test"
os.environ["EMAIL_DRY_RUN"] = "true"
os.environ.setdefault("SCHEDULER_ENABLED", "false")
# Razorpay is mocked at the client boundary below, but the credentials are pinned to
# placeholders as well. Belt and braces: the client refuses to make a network call with
# a placeholder key, so any path that slips past the mock fails instantly instead of
# quietly making 150 live API calls at the paced rate.
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_PLACEHOLDER"
os.environ["RAZORPAY_KEY_SECRET"] = "PLACEHOLDER"
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "eval-webhook-secret")
os.environ["RAZORPAY_MIN_REQUEST_INTERVAL_SECONDS"] = "0"

import psycopg  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Invoice, PaymentLink  # noqa: E402
from app.schemas.invoice import InvoiceIngestRow  # noqa: E402
from app.services.ingestion import ingest_batch  # noqa: E402
from eval.config import SIMULATION_DAYS  # noqa: E402
from eval.metrics import evaluate  # noqa: E402
from eval.report import print_comparison, print_main, print_violations, write_csv  # noqa: E402
from eval.simulator import Simulator  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
TABLES = (
    "audit_logs",
    "reconciliation_events",
    "promises",
    "reminders",
    "payment_links",
    "invoices",
    "customers",
    "merchants",
)


def ensure_database() -> None:
    url = make_url(settings.database_url)
    assert "eval" in (url.database or ""), f"refusing to run against {url.database!r}"

    admin = url.set(database="postgres").render_as_string(hide_password=False)
    admin = admin.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{url.database}"')

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")


def wipe() -> None:
    with Session(engine) as session:
        session.exec(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        session.commit()


def load_ledger(path: pathlib.Path) -> tuple[list[InvoiceIngestRow], dict[str, dict[str, str]]]:
    """Parse the held-out set, keeping the labels out of the database.

    The ground-truth columns are dropped by InvoiceIngestRow and kept here in memory,
    so nothing the system reads can see the answer it is being scored against.
    """
    rows: list[InvoiceIngestRow] = []
    truth: dict[str, dict[str, str]] = {}
    with path.open(newline="") as fh:
        for raw in csv.DictReader(fh):
            rows.append(InvoiceIngestRow.model_validate(raw))
            truth[raw["invoice_number"]] = {
                "reason": raw["ground_truth_reason"],
                "outcome": raw["ground_truth_outcome"],
            }
    return rows, truth


class _FakeRazorpay:
    """Stands in for Razorpay during evaluation.

    Mocked at the integration boundary — the seam that exists so the whole recovery
    loop can run without a network. Everything above it is production code: closure is
    still attempted, still audited, and still recorded on the link.
    """

    def cancel_payment_link(self, link_id: str):
        from app.integrations.razorpay_client import PaymentLinkResult

        return PaymentLinkResult.from_payload(
            {
                "id": link_id,
                "short_url": "https://rzp.io/eval",
                "reference_id": "eval",
                "status": "cancelled",
                "amount": 0,
                "amount_paid": 0,
            }
        )

    def fetch_payment_link(self, link_id: str):
        return self.cancel_payment_link(link_id)


def _mock_razorpay() -> None:
    import app.services.closure as closure_mod

    closure_mod.get_razorpay_client = lambda: _FakeRazorpay()  # type: ignore[assignment]


def fake_provision(session: Session) -> None:
    """Give every invoice a payment link without calling Razorpay.

    Mocked at the integration boundary, which is the whole reason that boundary exists.
    Everything above it — reconciliation, matching, the webhook handler — stays real.
    """
    for invoice in session.exec(select(Invoice)).all():
        session.add(
            PaymentLink(
                invoice_id=invoice.id,
                razorpay_payment_link_id=f"plink_EVAL_{invoice.invoice_number}",
                reference_id=f"vsl-{invoice.invoice_number}",
                short_url=f"https://rzp.io/eval/{invoice.invoice_number}",
                amount_expected_paise=invoice.amount_paise,
            )
        )
    session.commit()


def run_policy(policy: str, rows, truth, *, days: int, seed: int, use_llm: bool, limit: int | None):
    wipe()
    with Session(engine) as session:
        ingest_batch(session, rows[:limit] if limit else rows, rebase_dates=True)
        fake_provision(session)

        with TestClient(create_app()) as client:
            sim = Simulator(session, client, seed=seed, policy=policy, use_llm=use_llm)
            sim.load(truth)
            for day in range(days + 1):
                sim.run_day(day)

        object.__setattr__(settings, "demo_time_offset_days", 0)
        result = evaluate(
            session,
            truth,
            policy=policy,
            contacts_override=sim.naive_contacts if policy == "naive" else None,
        )

        if policy == "naive":
            # The schema refused to store these contacts, so the violation checker has
            # no rows to inspect and would report a clean run. Reporting zero breaches
            # for a policy that contacted people five times each would be a lie by
            # omission, so they are counted from simulator state instead.
            from app.core.constants import MAX_AUTOMATED_REMINDERS

            for state in sim.states.values():
                if state.contacts_received > MAX_AUTOMATED_REMINDERS:
                    result.violations.over_cap.append(
                        f"{state.invoice_number}: {state.contacts_received} contacts"
                    )
                if state.ground_truth_reason == "dispute_likely" and state.contacts_received:
                    result.violations.disputed_contacted.append(
                        f"{state.invoice_number}: {state.contacts_received} contacts on a "
                        "disputed invoice"
                    )
        return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default=str(DATA / "invoices_eval.csv"))
    ap.add_argument("--days", type=int, default=SIMULATION_DAYS)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--live-llm", action="store_true", help="use Gemini instead of the rules")
    ap.add_argument("--compare-baselines", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.set)
    if not path.exists():
        sys.exit(f"✗ {path} not found. Run: uv run python -m scripts.generate_synthetic")

    ensure_database()
    _mock_razorpay()
    rows, truth = load_ledger(path)
    print(f"Loaded {len(rows)} held-out invoices from {path.name}")
    print(
        f"Seed {args.seed} · {args.days} simulated days · "
        f"{'live Gemini' if args.live_llm else 'rules and templates'}"
    )

    started = time.time()
    policies = ["none", "naive", "vasooli"] if args.compare_baselines else ["vasooli"]
    results = [
        run_policy(
            p, rows, truth, days=args.days, seed=args.seed, use_llm=args.live_llm, limit=args.limit
        )
        for p in policies
    ]

    main_result = results[-1]
    print_main(main_result, days=args.days)
    clean = print_violations(main_result)

    if len(results) > 1:
        print_comparison(results)

    out = write_csv(results)
    print(f"\nWritten to {out}  ·  {time.time() - started:.1f}s")

    if not clean:
        print("\n✗ Compliance breaches detected — this is a failure, not a low score.")
        sys.exit(1)


if __name__ == "__main__":
    main()
