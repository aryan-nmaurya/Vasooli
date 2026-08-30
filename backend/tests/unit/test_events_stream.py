from app.services.events import after_id, publish


def test_event_fanout_has_monotonic_resume_ids():
    before = after_id(0)
    publish({"type": "invoice_recovered", "invoice_id": "test"})
    events = after_id(before[-1][0] if before else 0)
    assert events
    assert events[-1][1]["type"] == "invoice_recovered"
    assert events[-1][0] > (before[-1][0] if before else 0)
