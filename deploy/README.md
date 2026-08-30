# Deploying Vasooli

Frontend on Vercel, backend on EC2, PostgreSQL on encrypted RDS.

The split is deliberate. Vercel hosts Next.js for free, permanently, and does it
better than Amplify. AWS hosts the part Vercel cannot: a long-lived process. Vasooli
is not a request/response API — it carries an in-process APScheduler that runs the
recovery cycle at 10:00 IST and a payment sync hourly, and it takes a session-scoped
Postgres advisory lock while doing so. Anything that sleeps, scales to zero, or pools
connections in transaction mode breaks one of those.

---

## 1. The box

**EC2 and RDS, Mumbai (`ap-south-1`).** Use a 2 GB or larger EC2 instance and an
encrypted private RDS PostgreSQL 17 instance. Put them in the same VPC but different
failure domains; RDS should not be publicly accessible.

Mumbai keeps latency near the intended customers and Razorpay. Do not attach a NAT
Gateway or ALB for this single-host topology unless the architecture actually needs it.

```bash
# EC2 console → Ubuntu 24.04 → ap-south-1 → 2 GB+ RAM → encrypted EBS
# Allocate and attach an Elastic IP so DNS does not change after a restart.
```

**Security group** — open **80** and **443** publicly and restrict **22** to an
operator IP. Do not expose 5432.
Leave EC2 port 5432 closed. Give the RDS security group one inbound rule for 5432
whose source is the EC2 security group—not an IP range and never `0.0.0.0/0`.

---

## 2. DNS, before anything else

Point an A record at the static IP and let it propagate:

```
api.yourdomain.com.   A   <STATIC_IP>
```

Do this first. Caddy requests its certificate on first boot, and Let's Encrypt
validates by connecting back on port 80. A record that has not propagated looks
identical to a broken server, and failed attempts count against a limit of 5 per
domain per week.

```bash
dig +short api.yourdomain.com    # must print your static IP before you continue
```

No domain? One is ~₹700/year and you want one for the demo anyway. A free dynamic-DNS
subdomain also works — Let's Encrypt issues for those.

---

## 3. Deploy

```bash
ssh ubuntu@<STATIC_IP>

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker

git clone <your-repo> vasooli && cd vasooli/deploy
cp .env.example .env && chmod 600 .env
nano .env                      # fill everything; see section 4

docker compose -f docker-compose.rds.yml build api
docker compose -f docker-compose.rds.yml run --rm api alembic upgrade head
docker compose -f docker-compose.rds.yml run --rm api \
  python -m scripts.manage_operator create owner --display-name "Owner" --role admin
docker compose -f docker-compose.rds.yml up -d
docker compose -f docker-compose.rds.yml logs -f caddy   # watch the cert issue
```

The API container runs `alembic upgrade head` before binding, so migrations apply on
every deploy. A container that boots against an out-of-date schema fails in a far more
confusing way than one that refuses to start.

Redeploy after a push:

```bash
git pull && docker compose -f docker-compose.rds.yml up -d --build
```

### Deploy this remediation release

These steps deploy the retry, timeout, dispute-resume, inbound-message, runtime-banner,
responsive-header, and offline-fallback changes together. They do not trigger a
payment, email, AI request, or recovery cycle.

#### A. Prepare and publish the revision

Run locally from the repository root:

```bash
# Rebuild both copies of the offline fallback before committing.
cd backend
uv run --with pillow python ../scripts/generate_payment_webhook_fallback.py
cd ..

cd backend
uv run ruff check app tests alembic/versions
uv run ruff format --check app tests alembic/versions
uv run pytest -q
cd ../frontend
npm run lint
npx tsc --noEmit
npm test -- --run
npm run build -- --webpack
cd ..

git status --short
git add README.md Docs backend deploy frontend scripts
git commit -m "Harden recovery safety and add webhook fallback"
git push origin HEAD
```

Merge that revision into the branch connected to Vercel production (normally
`main`). Record the resulting commit SHA; use that same SHA on EC2 so the frontend and
backend describe the same API.

#### B. Update EC2 configuration safely

SSH to the existing EC2 host and update `deploy/.env`. Do not replace the file with
`.env.example`, because doing so would overwrite deployed secrets.

```bash
ssh ubuntu@<EC2_ELASTIC_IP>
cd /home/ubuntu/vasooli/deploy
chmod 600 .env
nano .env
```

Ensure these non-secret safety settings exist:

```dotenv
RAZORPAY_TIMEOUT_SECONDS=10
ALLOW_LIVE_RAZORPAY=false
ALLOW_DIRECT_CUSTOMER_EMAIL=false
EMAIL_REPLY_TO_DOMAIN=replies.yourdomain.com
EMAIL_PROVIDER_TIMEOUT_SECONDS=10
REQUIRE_OFFSITE_BACKUP=true
```

Keep `RAZORPAY_KEY_ID` on an `rzp_test_...` value. For the current safe demo, keep
either `EMAIL_DRY_RUN=true`, or keep `EMAIL_REDIRECT_TO` pointed at the operator inbox.
Do not set `ALLOW_DIRECT_CUSTOMER_EMAIL=true` merely to make startup succeed.

Native inbound email uses Resend Receiving. Put the webhook signing secret returned
by Resend here (the custom normalizer secret can remain empty):

```dotenv
RESEND_INBOUND_WEBHOOK_SECRET=whsec_...
INBOUND_EMAIL_WEBHOOK_SECRET=
```

In Resend, enable Receiving on `EMAIL_REPLY_TO_DOMAIN`, add the required MX record,
wait for status `verified` and capability `receiving: enabled`, then create an enabled
`email.received` webhook pointing at
`https://<DOMAIN>/api/webhooks/resend/inbound`. Never invent the `whsec_` value; copy
the signing secret Resend issues for that webhook.

#### C. Back up, update, migrate, and restart the backend

Still on EC2:

```bash
cd /home/ubuntu/vasooli/deploy

# The script writes a local dump and uploads it when BACKUP_S3_URI is configured.
./backup.sh

cd ..
git fetch origin
git checkout main
git pull --ff-only origin main
git rev-parse HEAD

cd deploy
docker compose -f docker-compose.rds.yml build api
docker compose -f docker-compose.rds.yml run --rm api alembic upgrade head
# First deployment only: create each human separately. The app refuses production
# startup when no active operator exists.
docker compose -f docker-compose.rds.yml run --rm api \
  python -m scripts.manage_operator create owner --display-name "Owner" --role admin
docker compose -f docker-compose.rds.yml up -d
```

The API container runs `alembic upgrade head` before Uvicorn starts. Confirm the new
inbound-message revision and service health:

```bash
docker compose -f docker-compose.rds.yml exec -T api alembic current
# Expected head: c82e9f7a4b10

docker compose -f docker-compose.rds.yml ps
docker compose -f docker-compose.rds.yml logs --since=10m api
curl --fail --silent --show-error https://api.vasooli.space/live
curl --fail --silent --show-error https://api.vasooli.space/health
```

If the API fails to become healthy, inspect the API logs and keep the existing database
volume intact. Do not run `docker compose down -v`—that deletes PostgreSQL data.

#### D. Deploy the frontend on Vercel

Verify the production variables before deploying:

```text
NEXT_PUBLIC_API_URL=https://api.vasooli.space
SESSION_SECRET=<exactly the same session secret as the backend>
```

Delete legacy `DASHBOARD_PASSWORD` and `ADMIN_API_KEY` variables from the Vercel
project if they exist. Human requests now carry the backend-issued named-operator
session; the long-lived service key must exist only on the backend host.

If Vercel is connected to Git, merging to its production branch deploys automatically.
Otherwise deploy from the repository root with the Vercel CLI:

```bash
npx vercel --cwd frontend
# Inspect the preview, then:
npx vercel --cwd frontend --prod
```

#### E. Post-deploy verification

Run these without starting a live recovery cycle:

```bash
curl --fail --silent --show-error \
  https://vasooli-phi.vercel.app/demo/payment-webhook-fallback.gif \
  --output /tmp/payment-webhook-fallback.gif
file /tmp/payment-webhook-fallback.gif

curl --fail --silent --show-error \
  https://api.vasooli.space/api/dashboard/runtime \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

Then sign in with a database operator account and verify:

1. The runtime banner says Razorpay **test**, shows the real scheduler/email modes,
   and reports inbound email as `native resend` after configuration.
2. At 375 px width, the header has no horizontal scrollbar.
3. “Resolve and resume recovery” refuses a blank note and presents a confirmation.
4. Opening `/demo/payment-webhook-fallback.gif` works without a dashboard session.
5. `docker compose logs api` shows the one-minute retry sweep without repeated errors.

Do not press **Run recovery cycle** during deployment verification. Use **Dry run** only
if a policy-cycle check is needed.

Exercise the provider contracts from the API image. The Razorpay check creates a ₹1
test-mode link and cancels it; the second command validates the receiving-enabled
domain, enabled inbound webhook, sends one redirected verification email, and makes
one bounded structured Gemini request:

```bash
docker compose -f docker-compose.rds.yml run --rm api python -m scripts.check_razorpay
docker compose -f docker-compose.rds.yml run --rm api \
  python -m scripts.verify_live_integrations \
  --expected-webhook-url "https://${DOMAIN}/api/webhooks/resend/inbound" \
  --send-test-email
```

Finally, send a reply from the exact customer email on a controlled test invoice to
`invoice-<invoice-number>@<EMAIL_REPLY_TO_DOMAIN>`. Confirm the dashboard conversation
shows the full text and the API log reports `status=processed`. This is the only check
that proves MX delivery, Resend Receiving, signature verification, authenticated body
retrieval, routing, persistence, and reply processing together.

---

## 4. Environment

### AWS box — `deploy/.env`

Everything is in `.env.example`. Generate the secrets rather than inventing them:

```bash
openssl rand -base64 24   # POSTGRES_PASSWORD
openssl rand -hex 32      # ADMIN_API_KEY
openssl rand -hex 32      # SESSION_SECRET
```

Two that decide whether this is safe to run:

| Variable | Set it to | Why |
|---|---|---|
| `EMAIL_REDIRECT_TO` | your own inbox | The seeded ledger has real-looking customer addresses. With `EMAIL_DRY_RUN=false` and this empty, one cycle emails all of them. |
| `SCHEDULER_ENABLED` | `true` on exactly one host | Two schedulers means two cycles. The advisory lock would stop the double-send, but do not spend that protection casually. |

### Vercel

Project → Settings → Environment Variables:

| Variable | Value | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.yourdomain.com` | Public by design — it is only a URL. |
| `SESSION_SECRET` | same as the backend | The backend issues the named-user token; Vercel verifies and forwards it but never mints an identity. |

`ADMIN_API_KEY` remains a service credential for scripts and smoke checks. The
dashboard no longer uses it for human traffic.

Node version: `frontend/package.json` declares `engines.node: ">=22"`, which Vercel
reads. Nothing to configure.

---

## 5. Razorpay webhook

Dashboard → Settings → Webhooks:

- URL: `https://api.yourdomain.com/api/webhooks/razorpay`
- Secret: the same `RAZORPAY_WEBHOOK_SECRET` in `deploy/.env`
- Events: `payment_link.paid`, `payment_link.partially_paid`

The HMAC is computed over the raw request body, so the secret must match exactly and
the reverse proxy must not touch the bytes. `Caddyfile` is written accordingly.

If a webhook is ever missed, `sync_payment_links()` asks Razorpay directly on the
hourly job and reconciles through the same path. Webhooks are the fast path, not the
only one.

---

## 6. Verify

```bash
./smoke.sh https://api.yourdomain.com "$ADMIN_API_KEY"
```

Eight checks: valid TLS, liveness, readiness including the database, that the
dashboard is gated without a key and reachable with one, the disputes endpoint, that
an unsigned webhook is refused, and a dry-run recovery cycle that exercises the whole
cadence and contacts nobody.

A green `/health` alone proves almost nothing — it does not tell you the signature
check is running or that the scheduler's work executes against this database.

---

## 7. Backups and cost control

```bash
crontab -e
0 3 * * * /home/ubuntu/vasooli/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
```

Keeps 14 nightly dumps locally and uploads every dump to a private, encrypted,
versioned S3 prefix. With `REQUIRE_OFFSITE_BACKUP=true`, the job fails if S3 is not
configured. Run a restore drill weekly; it creates a uniquely named throwaway
database, validates invoice and audit tables, and drops only that validated target:

```bash
15 4 * * 0 /home/ubuntu/vasooli/deploy/restore-drill.sh >> /home/ubuntu/restore-drill.log 2>&1
```

**Set AWS Budgets alerts at $20 / $60 / $100 on day one.** Budgets is free. Also
confirm which credit you hold: accounts created after July 2025 are on the Free Plan,
where credits expire after 6 months and the account then closes and **erases
resources** after a grace period. If this needs to outlive the credit, switch to the
Paid Plan before it lapses.

Create two external dead-man checks before enabling the scheduler:

- `OPS_HEARTBEAT_URL`: 10-minute grace; the app pings every five minutes.
- `OPS_RECOVERY_HEARTBEAT_URL`: daily schedule shortly after 10:00 IST with a generous
  runtime grace; it is pinged only after the recovery cycle completes successfully.

Route both failures to an email/SMS destination outside EC2. A local log is not an
alert when the host holding the log is the thing that failed.

---

## RDS provisioning checklist

1. Create PostgreSQL 17 as Multi-AZ RDS in `ap-south-1`, private access only, encrypted
   storage, automated backups enabled, and deletion protection on.
2. Allow inbound 5432 only from the EC2 security group.
3. Create a dedicated application database/user and set
   `DATABASE_URL=postgresql://.../vasooli?sslmode=require` in `deploy/.env`.
4. Use `docker-compose.rds.yml`; do not start `docker-compose.prod.yml` on the same
   host, because that would quietly put PostgreSQL back in the EC2 failure domain.
5. Run `backup.sh`, then `restore-drill.sh`, and retain both successful logs before
   calling the migration complete.

**Do not put RDS Proxy in front of it.** `app/services/recovery.py` holds a
session-scoped `pg_try_advisory_lock` across the cycle. Transaction-mode
pooling either breaks or pins that, and the failure is silent —
the cycle simply stops running. One process with a handful of connections does not need
a proxy.
