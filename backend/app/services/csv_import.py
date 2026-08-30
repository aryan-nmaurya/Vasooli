"""Parse an uploaded ledger CSV into validated rows. Import.

Deliberately thin. All the hard parts of ingestion — per-row SAVEPOINT isolation,
duplicate skipping, customer upsert, date rebasing — already live in
`app.services.ingestion.ingest_batch` and are well covered. This only turns bytes into
`InvoiceIngestRow` objects and reports, per line, what could not be turned.

The line numbers matter more than they look. A merchant importing four hundred rows
needs to know that row 47 is wrong, not that "a row" is wrong; without the number they
have to bisect the file by hand.
"""

import csv
import io
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.schemas.invoice import InvoiceIngestRow

#: Human column headings the exports produce, mapped back to schema field names.
#:
#: Exports are written for a person to read — "Amount (₹)", not "amount_inr" — and an
#: export that cannot be re-imported is a one-way door. Rather than uglify the export
#: or keep a second machine-readable one, the importer learns both spellings.
#:
#: Keys are normalised: lowercased, with everything but letters and digits removed, so
#: "Amount (₹)", "amount_inr" and "Amount INR" all arrive here as "amountinr" or
#: "amount".
COLUMN_ALIASES = {
    "invoice": "invoice_number",
    "invoiceno": "invoice_number",
    "invoicenumber": "invoice_number",
    "customer": "customer_name",
    "customername": "customer_name",
    "email": "customer_email",
    "customeremail": "customer_email",
    "phone": "customer_phone",
    "customerphone": "customer_phone",
    "amount": "amount_inr",
    "amountinr": "amount_inr",
    "amount₹": "amount_inr",
    "issued": "issued_at",
    "issuedat": "issued_at",
    "issuedate": "issued_at",
    "due": "due_at",
    "dueat": "due_at",
    "duedate": "due_at",
}


def _normalise(header: str) -> str:
    """A header reduced to the form COLUMN_ALIASES is keyed on."""
    return "".join(c for c in header.lower() if c.isalnum())


def canonical_header(header: str) -> str:
    """The schema field name a column heading refers to, or the heading itself."""
    cleaned = header.strip()
    if cleaned in InvoiceIngestRow.model_fields:
        return cleaned
    return COLUMN_ALIASES.get(_normalise(cleaned), cleaned)


#: Big enough for a real receivables book, small enough that a mis-uploaded video does
#: not become a memory problem. The HTTP layer caps the body separately.
MAX_ROWS = 5_000


@dataclass
class RowProblem:
    """One line that could not be parsed, and why."""

    #: 1-based and counted as a spreadsheet counts: the header is line 1, so this is
    #: the number shown in Excel's gutter.
    line: int
    invoice_number: str
    message: str


@dataclass
class ParsedLedger:
    rows: list[InvoiceIngestRow] = field(default_factory=list)
    problems: list[RowProblem] = field(default_factory=list)
    #: Column names present in the file but not part of the schema. Reported rather
    #: than rejected — generator bookkeeping columns are expected and ignored.
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and not self.problems


class LedgerFileError(ValueError):
    """The file itself is unusable, as distinct from individual rows being wrong."""


def template_csv() -> bytes:
    """A blank file with exactly the columns the parser accepts.

    Generated from the schema rather than written by hand, so it cannot drift away
    from what the importer will actually take — a template that disagrees with the
    parser is worse than no template.
    """
    fields = list(InvoiceIngestRow.model_fields.keys())
    example = {
        "invoice_number": "INV-1001",
        "customer_name": "Acme Traders",
        "customer_email": "accounts@acme.example.com",
        "customer_phone": "+919876543210",
        "amount_inr": "42000.00",
        "issued_at": "2026-07-01",
        "due_at": "2026-07-31",
        "terms_days": "30",
        "customer_total_invoices": "12",
        "customer_invoices_paid_late": "2",
        "customer_invoices_defaulted": "0",
        "customer_broken_promises": "0",
        "customer_avg_invoice_inr": "38000.00",
        "has_prior_dispute_note": "false",
    }

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerow({f: example.get(f, "") for f in fields})
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def parse_ledger(payload: bytes) -> ParsedLedger:
    """Turn an uploaded CSV into rows, collecting per-line failures rather than
    stopping at the first one.

    Stopping early would mean a merchant fixes one row, re-uploads, and discovers the
    next — turning a five-minute correction into an afternoon.
    """
    try:
        # utf-8-sig: Excel writes a BOM, and without stripping it the first column is
        # named "﻿invoice_number" and every row fails on a missing field.
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LedgerFileError(
            "That file is not UTF-8 text. Export it from your spreadsheet as CSV."
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise LedgerFileError("The file is empty, or has no header row.")

    # Aliased before anything else, so a file exported from this system is judged
    # by what its columns mean rather than how they are spelled for a human.
    headers = [canonical_header(h) for h in reader.fieldnames if h]
    known = set(InvoiceIngestRow.model_fields)
    required = {name for name, f in InvoiceIngestRow.model_fields.items() if f.is_required()}
    missing = sorted(required - set(headers))
    if missing:
        raise LedgerFileError(
            "The file is missing required columns: "
            + ", ".join(missing)
            + ". Download the template to see the expected format."
        )

    result = ParsedLedger(unknown_columns=sorted(set(headers) - known))

    for index, raw in enumerate(reader, start=2):  # line 1 is the header
        if len(result.rows) >= MAX_ROWS:
            raise LedgerFileError(
                f"More than {MAX_ROWS:,} rows. Split the file and import in parts."
            )

        # Blank trailing lines are normal in exported spreadsheets, not errors.
        if not any((v or "").strip() for v in raw.values()):
            continue

        cleaned = {
            canonical_header(k): (v.strip() if isinstance(v, str) else v)
            for k, v in raw.items()
            if k
        }
        # Empty optional cells arrive as "" and would fail type coercion; dropping them
        # lets the schema's own defaults apply.
        cleaned = {k: v for k, v in cleaned.items() if v not in ("", None)}

        try:
            result.rows.append(InvoiceIngestRow.model_validate(cleaned))
        except ValidationError as exc:
            first = exc.errors()[0]
            field_name = ".".join(str(p) for p in first.get("loc", ())) or "row"
            result.problems.append(
                RowProblem(
                    line=index,
                    invoice_number=cleaned.get("invoice_number", "—"),
                    message=f"{field_name}: {first.get('msg', 'invalid')}",
                )
            )

    if not result.rows and not result.problems:
        raise LedgerFileError("No data rows found — the file has a header and nothing else.")

    return result
