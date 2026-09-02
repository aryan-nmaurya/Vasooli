from datetime import UTC, datetime

import pytest

from app.integrations.erp import adapter_for
from app.services.policy_versions import validate_policy


def test_policy_rejects_cadence_gap_shorter_than_cooldown():
    with pytest.raises(ValueError, match="Day 7 is only 4 days"):
        validate_policy(
            tier_offsets=[3, 7, 14], cooldown_days=7, max_attempts=3, timezone="Asia/Kolkata"
        )


def test_policy_preset_cadence_is_valid():
    validate_policy(
        tier_offsets=[3, 7, 14], cooldown_days=4, max_attempts=3, timezone="Asia/Kolkata"
    )


def test_custom_adapter_cursor_is_incremental_and_deterministic():
    adapter = adapter_for(
        "zoho",
        [
            {
                "source_id": "inv-1",
                "invoice_number": "INV-1",
                "customer_name": "Buyer",
                "customer_email": "buyer@example.com",
                "amount_paise": 1000,
                "issued_at": datetime.now(UTC),
                "due_at": datetime.now(UTC),
            },
            {
                "source_id": "inv-2",
                "invoice_number": "INV-2",
                "customer_name": "Buyer",
                "customer_email": "buyer@example.com",
                "amount_paise": 2000,
                "issued_at": datetime.now(UTC),
                "due_at": datetime.now(UTC),
            },
        ],
    )
    first = adapter.fetch_invoices(cursor=None, limit=1)
    second = adapter.fetch_invoices(cursor=first.next_cursor, limit=1)
    assert [row.source_id for row in first.records] == ["inv-1"]
    assert [row.source_id for row in second.records] == ["inv-2"]
    assert second.next_cursor is None
