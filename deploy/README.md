# Deploying Vasooli

Frontend on Vercel. Backend and Postgres on one AWS box.

The split is deliberate. Vercel hosts Next.js for free, permanently, and does it
better than Amplify. AWS hosts the part Vercel cannot: a long-lived process. Vasooli
is not a request/response API — it carries an in-process APScheduler that runs the
recovery cycle at 10:00 IST and a payment sync hourly, and it takes a session-scoped
Postgres advisory lock while doing so. Anything that sleeps, scales to zero, or pools
connections in transaction mode breaks one of those.

---

## 1. The box

**Lightsail, 2 GB RAM, Mumbai (`ap-south-1`).** ~$12/month, flat.

Lightsail rather than EC2 for one reason: the bill is a fixed number. EC2 is the same
machine with metered egress and a console that will happily attach a NAT Gateway
(~$32/month) or an ALB (~$17/month) to a project that needs neither. On $120 of
credit, one accidental NAT is a third of your runway.

Mumbai because your customers and Razorpay are in India.

```bash
# Lightsail console → Create instance → Linux/Unix → OS Only → Ubuntu 24.04
# Plan: $12/mo (2 GB RAM, 2 vCPU, 60 GB SSD)
# Then attach a STATIC IP (free while attached; billed only if you leave it orphaned)
```

**Firewall** — Lightsail console → Networking. Open **80** and **443** only.
Leave 5432 closed. Nothing in `docker-compose.prod.yml` publishes it, but an open
port plus the default `vasooli:vasooli` credentials is found by scanners within hours.

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

docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f caddy   # watch the cert issue
```

The API container runs `alembic upgrade head` before binding, so migrations apply on
every deploy. A container that boots against an out-of-date schema fails in a far more
confusing way than one that refuses to start.

Redeploy after a push:

```bash
git pull && docker compose -f docker-compose.prod.yml up -d --build
```

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
| `ADMIN_API_KEY` | same as the box | **No `NEXT_PUBLIC_` prefix.** That prefix ships it in the browser bundle. |
| `SESSION_SECRET` | a *different* `openssl rand -hex 32` | The frontend mints its own `vasooli_dash` cookie and verifies only that. It does **not** need to match the backend's. |

`DASHBOARD_PASSWORD` lives on the backend only — the frontend forwards the password
there and never checks it locally.

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

Keeps 14 nightly dumps on the box. That covers a bad migration or a wrong `DELETE`;
it does not cover losing the instance. Push them to S3 once this matters.

**Set AWS Budgets alerts at $20 / $60 / $100 on day one.** Budgets is free. Also
confirm which credit you hold: accounts created after July 2025 are on the Free Plan,
where credits expire after 6 months and the account then closes and **erases
resources** after a grace period. If this needs to outlive the credit, switch to the
Paid Plan before it lapses.

---

## Moving Postgres to RDS later

Provision `db.t4g.micro`, then in `deploy/.env` set `DATABASE_URL` to the RDS endpoint
and delete the `db` service and its `depends_on` from `docker-compose.prod.yml`. The
application code does not change.

**Do not put RDS Proxy in front of it.** `app/services/recovery.py` runs
`SET idle_session_timeout` and holds a session-scoped `pg_try_advisory_lock` across the
cycle. Transaction-mode pooling either breaks or pins that, and the failure is silent —
the cycle simply stops running. One process with a handful of connections does not need
a proxy.
