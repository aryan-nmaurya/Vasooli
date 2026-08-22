# Vasooli

**AI-powered B2B receivables recovery agent.** Chase. Track. Reconcile. Recover.

Vasooli ingests overdue B2B invoices, diagnoses why each is at risk, chases them on a
bounded and compliant escalation schedule, tracks the promises customers make to pay,
and reconciles real incoming payments the moment Razorpay confirms them.

- Product spec: `Docs/Vasooli_Documentation.md`
- Build plan: `Docs/Vasooli_Implementation_Plan.md`

**Current status: Phase 0 complete** — foundation, config, logging, health, CI.

---

## Quick start

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
cp .env.example .env      # then fill in the values
```

**Database** — either use the local Postgres you already have:

```bash
createdb vasooli
```

...or start one in Docker (leave `DATABASE_URL` pointed at `localhost:5432`):

```bash
docker compose up -d db
```

**Run it:**

```bash
uv run uvicorn app.main:app --reload   # from backend/
```

- Health: http://localhost:8000/health
- API docs: http://localhost:8000/docs

---

## Development

```bash
uv run pytest -q          # tests
uv run ruff check .       # lint
uv run ruff format .      # format
uv run alembic upgrade head   # apply migrations
```

---

## Layout

```
backend/
  app/
    core/          config, constants, clock, money, logging, db — no business logic
    models/        SQLModel entities                            (Phase 1)
    schemas/       API request/response DTOs                    (Phase 2)
    policy/        deterministic decisions — pure, no I/O        (Phase 5)
    ai/            LLM tasks — advisory only, cannot send        (Phase 6)
    integrations/  Razorpay, email, Gemini transport             (Phase 3, 6, 7)
    services/      orchestration — the only layer that writes
    api/           HTTP routers (health.py so far)
    scheduler/     APScheduler jobs                              (Phase 8)
  alembic/         migrations; URL comes from app settings, not alembic.ini
  eval/            evaluation harness                            (Phase 11)
  tests/           unit / integration / architecture
  scripts/
frontend/          Next.js dashboard                             (Phase 10)
Docs/              product spec + implementation plan
```

Layers are ordered by what they may import, and the rule is enforced by
`tests/architecture/test_layering.py` rather than by review: `ai/` cannot reach the
email sender or Razorpay, and `policy/` cannot reach the database, the network, or a
clock. That makes "the model recommends, deterministic code decides" a property of the
import graph instead of a comment.

## Conventions

These are load-bearing — the plan's later phases assume them.

| Rule | Why |
|---|---|
| Money is **integer paise**, never float | Float rupees produce reconciliation mismatches; Razorpay's API is already paise |
| Cadence values (3 / 10 / 21) live only in `app/core/constants.py` | Enforced by a test; the plan's §0.1 rule |
| Schema changes go through Alembic — no `create_all()` | Otherwise local and deployed schemas diverge silently |
| All timestamps `TIMESTAMPTZ`, stored UTC; day math in `Asia/Kolkata` | Overdue counts must match what the merchant sees |
| `EMAIL_DRY_RUN=true` until Phase 7 passes | Synthetic customers must never reach real inboxes |
| The LLM never sends, never writes invoice status | The policy engine decides; the model only drafts and explains |

## Configuration

All settings are in `app/core/config.py`. A missing required variable fails at import
with a message naming it — the app will not boot half-configured.

### AI provider

Google AI Studio, with a three-step failover chain:

| Step | Model | Triggered by |
|---|---|---|
| 1 | `gemini-3.7-flash` (`GEMINI_PRIMARY_MODEL`) | — |
| 2 | `gemini-3.6-flash` (`GEMINI_FALLBACK_MODEL`) | RPM/RPD quota, timeout, 5xx, or a schema-validation failure that survives one repair attempt |
| 3 | Rule-based diagnosis + templated copy | both models unavailable |

Step 3 is why a quota wall is a footnote rather than a broken demo: the four reason
categories are defined as rules, so the model supplies explanation quality, not core
capability. Every failover is written to the audit log.

Model IDs are config, not code — a retired or mistyped ID is a `.env` edit. Verify the
exact IDs your Google AI Studio key serves before Phase 6.
