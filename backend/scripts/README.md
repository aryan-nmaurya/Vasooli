# scripts/

Run these as modules from `backend/`, e.g. `uv run python -m scripts.seed`.

| Script | Purpose |
|---|---|
| `check_razorpay.py` | Pre-flight: can this account create Payment Links? |
| `generate_synthetic.py` | Write the demo and eval ledgers + reply fixtures |
| `seed.py` | Load a ledger CSV into the database, idempotently |
| `demo_reset.py` | Wipe and seed the curated 8-invoice demo set, with payment links |
| `replay_webhook.py` | Sign and POST a Razorpay-shaped payload locally (**demo simulation**) |
| `demo_dispute.py` | Inject a disputed reply, show the pause, then resolve it (**demo simulation**) |

`replay_webhook.py` builds a payload in Razorpay's shape and signs it with the real
webhook secret. It proves our handling is correct; it does **not** prove Razorpay sends
what we think. Use a genuine test payment for that at least once.
