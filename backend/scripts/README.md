# scripts/

Run these as modules from `backend/`, e.g. `uv run python -m scripts.seed`.

| Script | Purpose |
|---|---|
| `check_razorpay.py` | Pre-flight: is Smart Collect usable on this account? Run before Phase 3 |
| `generate_synthetic.py` | Write the demo and eval ledgers + reply fixtures (Phase 2) |
| `seed.py` | Load a ledger CSV into the database, idempotently (Phase 2) |
| `replay_webhook.py` | Sign and POST a saved Razorpay payload locally (Phase 4) |
| `demo_reset.py` | Wipe, seed, provision, fast-forward to demo state (Phase 13) |
