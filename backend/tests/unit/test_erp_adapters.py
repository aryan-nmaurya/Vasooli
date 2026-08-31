from app.integrations.erp import _invoice_from_mapping


def test_zoho_invoice_identity_uses_the_provider_invoice_id():
    row = {
        "invoice_id": "982000000567114",
        "invoice_number": "INV-0042",
        "customer_name": "Buyer Ltd",
        "customer_email": "ap@buyer.example",
        "total": "1250.50",
        "date": "2026-08-01",
        "due_date": "2026-08-31",
    }

    invoice = _invoice_from_mapping("zoho", row, tenant="organization-1")

    assert invoice.source_id == "982000000567114"
    assert invoice.invoice_number == "INV-0042"
    assert invoice.amount_paise == 125050
