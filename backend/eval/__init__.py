"""Evaluation harness. Phase 11.

Runs the REAL policy engine, scheduler cycle, and reconciliation path against a
simulated clock and simulated customers, with Razorpay and email mocked at the
integration boundary. Metrics are imported from `app.services.metrics`, never
reimplemented — two implementations drift, and the dashboard disagreeing with the
eval report on stage is a bad moment.
"""
