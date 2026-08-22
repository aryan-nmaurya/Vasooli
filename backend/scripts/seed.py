"""Load a synthetic ledger into the database. Phase 2.

    uv run python -m scripts.seed                       # demo set, dates rebased to today
    uv run python -m scripts.seed --file data/invoices_eval.csv
    uv run python -m scripts.seed --no-rebase           # keep the CSV's absolute dates

Idempotent: re-running reports the rows as duplicates and changes nothing, so it is
safe to run between demo rehearsals.
"""

import argparse
import csv
import pathlib
import sys

from pydantic import ValidationError
from sqlmodel import Session

from app.core.db import engine
from app.core.money import format_inr
from app.schemas.invoice import InvoiceIngestRow
from app.services.ingestion import ingest_batch

DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data"


def load_rows(path: pathlib.Path) -> list[InvoiceIngestRow]:
    """Parse a ledger CSV.

    Ground-truth and generator-metadata columns present in the file are dropped here
    by InvoiceIngestRow's `extra="ignore"` — they exist for the Phase 11 eval and must
    never reach the database.
    """
    rows: list[InvoiceIngestRow] = []
    errors: list[str] = []

    with path.open(newline="") as fh:
        for lineno, raw in enumerate(csv.DictReader(fh), start=2):
            try:
                rows.append(InvoiceIngestRow.model_validate(raw))
            except ValidationError as exc:
                errors.append(f"  line {lineno} ({raw.get('invoice_number', '?')}): {exc}")

    if errors:
        print(f"✗ {len(errors)} row(s) failed validation:", file=sys.stderr)
        print("\n".join(errors[:10]), file=sys.stderr)
        sys.exit(1)

    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=str(DATA_DIR / "invoices_demo.csv"))
    ap.add_argument(
        "--no-rebase",
        action="store_true",
        help="Keep the CSV's absolute due dates instead of shifting them onto today.",
    )
    args = ap.parse_args()

    path = pathlib.Path(args.file)
    if not path.exists():
        print(f"✗ {path} not found. Run: uv run python -m scripts.generate_synthetic")
        sys.exit(1)

    rows = load_rows(path)
    print(f"Parsed {len(rows)} rows from {path.name}")

    with Session(engine) as session:
        report = ingest_batch(session, rows, rebase_dates=not args.no_rebase)

    total = sum(r.amount_paise for r in rows)
    print(
        f"\n  ingested            {report.ingested}"
        f"\n  skipped (duplicate) {report.skipped_duplicates}"
        f"\n  failed              {report.failed}"
        f"\n  customers created   {report.customers_created}"
        f"\n  ledger value        {format_inr(total)}"
    )
    for err in report.errors[:10]:
        print(f"    ✗ {err.invoice_number}: {err.error}")

    if report.ingested:
        print("\nNext: uv run uvicorn app.main:app --reload  →  GET /api/invoices")


if __name__ == "__main__":
    main()
