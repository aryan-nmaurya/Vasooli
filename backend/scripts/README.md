# scripts/

Operational scripts. Populated as phases land:

- `reset_db.py`         — drop, migrate, seed (Phase 1)
- `generate_synthetic.py` — synthetic invoice + reply fixtures (Phase 2)
- `replay_webhook.py`   — sign and POST a saved Razorpay payload locally (Phase 4)
- `demo_reset.py`       — wipe, seed, provision, fast-forward to demo state (Phase 13)
