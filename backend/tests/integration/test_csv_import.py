"""Ledger import from CSV.

The parsing is the only new logic — the write is `ingest_batch`, which has its own
coverage. So these pin the things a merchant actually depends on: that a preview never
writes, that a bad row is reported by its spreadsheet line number, and that a file
exported from this system imports back into it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.core.config import settings
from app.main import create_app
from app.models import Invoice
from app.services.csv_import import LedgerFileError, canonical_header, parse_ledger, template_csv

HEADER = "invoice_number,customer_name,customer_email,amount_inr,issued_at,due_at"
GOOD = f"{HEADER}\nINV-5001,Acme,ap@acme.example.com,42000.00,2026-07-01,2026-07-31"


@pytest.fixture
def api(session):
    with TestClient(create_app()) as c:
        yield c


def upload(api, csv_text: str, *, dry_run: bool = True):
    return api.post(
        "/api/invoices/import",
        files={"file": ("ledger.csv", csv_text.encode(), "text/csv")},
        data={"dry_run": str(dry_run).lower()},
        headers={"X-Admin-Key": settings.admin_api_key},
    )


# ===========================================================================
# A preview never writes.
# ===========================================================================


def test_a_preview_reports_without_importing(api, session):
    """The whole reason for two steps. If a preview could write, it would not be one."""
    body = upload(api, GOOD, dry_run=True).json()

    assert body["dry_run"] is True
    assert body["would_import"] == 1
    assert session.exec(select(Invoice)).all() == []


def test_confirming_actually_writes(api, session):
    body = upload(api, GOOD, dry_run=False).json()

    assert body["result"]["ingested"] == 1
    numbers = {i.invoice_number for i in session.exec(select(Invoice)).all()}
    assert "INV-5001" in numbers


def test_importing_the_same_file_twice_changes_nothing(api, session):
    upload(api, GOOD, dry_run=False)
    second = upload(api, GOOD, dry_run=False).json()["result"]

    assert second["ingested"] == 0
    assert second["skipped_duplicates"] == 1
    assert len(session.exec(select(Invoice)).all()) == 1


# ===========================================================================
# Bad rows are located, not just counted.
# ===========================================================================


def test_a_bad_row_is_reported_by_its_spreadsheet_line(api, session):
    """ "A row is invalid" makes someone bisect the file by hand. The line number is
    the whole value of the message."""
    csv_text = (
        f"{HEADER}\n"
        "INV-5001,Acme,ap@acme.example.com,42000.00,2026-07-01,2026-07-31\n"
        "INV-5002,Broken,not-an-email,1000.00,2026-07-01,2026-07-31"
    )
    body = upload(api, csv_text).json()

    assert body["parsed"] == 1
    assert len(body["problems"]) == 1
    problem = body["problems"][0]
    # Line 1 is the header, so the bad row is line 3 — the number Excel shows.
    assert problem["line"] == 3
    assert problem["invoice_number"] == "INV-5002"
    assert "email" in problem["message"]


def test_good_rows_still_import_alongside_a_bad_one(api, session):
    """One malformed row must not cost the merchant the other 399."""
    csv_text = (
        f"{HEADER}\n"
        "INV-5001,Acme,ap@acme.example.com,42000.00,2026-07-01,2026-07-31\n"
        "INV-5002,Broken,not-an-email,1000.00,2026-07-01,2026-07-31"
    )
    result = upload(api, csv_text, dry_run=False).json()["result"]
    assert result["ingested"] == 1


def test_duplicates_are_named_before_the_write(api, session):
    """So "12 imported, 8 skipped" is never a surprise after the fact."""
    upload(api, GOOD, dry_run=False)
    body = upload(api, GOOD, dry_run=True).json()

    assert body["duplicates"] == ["INV-5001"]
    assert body["would_import"] == 0


# ===========================================================================
# Files that are wrong as files, rather than row by row.
# ===========================================================================


def test_a_file_missing_required_columns_is_refused_with_the_names(api, session):
    res = upload(api, "invoice_number,customer_name\nINV-1,Acme")
    assert res.status_code == 422
    assert "customer_email" in res.json()["detail"]


def test_an_empty_file_is_refused(api, session):
    assert upload(api, "").status_code == 422


def test_a_header_with_no_rows_is_refused(api, session):
    res = upload(api, HEADER)
    assert res.status_code == 422
    assert "nothing else" in res.json()["detail"]


def test_blank_trailing_lines_are_not_errors(api, session):
    """Spreadsheets export them constantly; treating them as bad rows is noise."""
    body = upload(api, GOOD + "\n\n\n").json()
    assert body["parsed"] == 1
    assert body["problems"] == []


def test_a_non_utf8_file_gets_a_useful_message(api, session):
    res = api.post(
        "/api/invoices/import",
        files={"file": ("ledger.csv", b"\xff\xfe\x00bad", "text/csv")},
        data={"dry_run": "true"},
        headers={"X-Admin-Key": settings.admin_api_key},
    )
    assert res.status_code == 422
    assert "UTF-8" in res.json()["detail"]


# ===========================================================================
# Round-trip with the exports.
# ===========================================================================


def test_the_template_parses_with_its_own_parser(api, session):
    """A template that disagrees with the parser is worse than no template."""
    parsed = parse_ledger(template_csv())
    assert parsed.ok
    assert len(parsed.rows) == 1


def test_the_template_endpoint_serves_a_csv(api, session):
    res = api.get("/api/invoices/import/template", headers={"X-Admin-Key": settings.admin_api_key})
    assert res.status_code == 200
    assert "attachment" in res.headers["content-disposition"]
    assert b"invoice_number" in res.content


def test_an_exported_ledger_imports_back(api, session):
    """Exports are written for a person to read — "Amount (₹)", not "amount_inr". An
    export that cannot be re-imported is a one-way door, so the importer knows both
    spellings."""
    upload(api, GOOD, dry_run=False)

    exported = api.get(
        "/api/export/invoices?format=csv", headers={"X-Admin-Key": settings.admin_api_key}
    ).content.decode("utf-8-sig")

    parsed = parse_ledger(exported.encode())
    assert parsed.problems == []
    assert {r.invoice_number for r in parsed.rows} == {"INV-5001"}


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Invoice", "invoice_number"),
        ("Customer", "customer_name"),
        ("Email", "customer_email"),
        ("Amount (₹)", "amount_inr"),
        ("Issued", "issued_at"),
        ("Due", "due_at"),
        ("invoice_number", "invoice_number"),
        ("Payment link", "Payment link"),  # unknown headings pass through untouched
    ],
)
def test_export_headings_map_back_to_schema_fields(heading, expected):
    assert canonical_header(heading) == expected


def test_an_oversized_row_count_is_refused():
    from app.services.csv_import import MAX_ROWS

    rows = "\n".join(
        f"INV-{i},Acme,ap@acme.example.com,1000.00,2026-07-01,2026-07-31"
        for i in range(MAX_ROWS + 2)
    )
    with pytest.raises(LedgerFileError):
        parse_ledger(f"{HEADER}\n{rows}".encode())


# ===========================================================================
# Who may do this.
# ===========================================================================


def test_an_auditor_may_export_but_not_import(api, session, operator_account):
    """The two halves are not symmetric, and the README says so. An auditor's job is
    to take evidence out of the system, so export stays open to them; the import
    commit is a write and is refused."""
    from tests.integration.test_auth import TEST_OPERATOR_PASSWORD, TEST_OPERATOR_USERNAME

    operator_account.role = "auditor"
    session.add(operator_account)
    session.commit()
    api.post(
        "/api/auth/login",
        json={"username": TEST_OPERATOR_USERNAME, "password": TEST_OPERATOR_PASSWORD},
    )

    assert api.get("/api/export/invoices?format=csv").status_code == 200
    assert api.get("/api/invoices/import/template").status_code == 200
    # Even a preview is a POST, so it is refused — the role check is on the method.
    assert (
        api.post(
            "/api/invoices/import",
            files={"file": ("ledger.csv", GOOD.encode(), "text/csv")},
            data={"dry_run": "true"},
        ).status_code
        == 403
    )


def test_anonymous_callers_get_nothing(api, session):
    assert api.get("/api/export/invoices?format=csv").status_code == 401
    assert api.get("/api/invoices/import/template").status_code == 401
    assert (
        api.post(
            "/api/invoices/import", files={"file": ("ledger.csv", GOOD.encode(), "text/csv")}
        ).status_code
        == 401
    )
