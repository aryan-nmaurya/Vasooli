#!/usr/bin/env bash
# Post-deploy verification. Run from anywhere:
#   ./smoke.sh https://api.yourdomain.com "$ADMIN_API_KEY"
#
# Checks the things that are actually load-bearing for this system, in the order they
# would break: TLS, process, database, auth, the scheduler's own entry point, and the
# webhook's signature rejection. A green /health alone proves almost nothing.
set -uo pipefail

BASE="${1:?usage: ./smoke.sh https://api.example.com ADMIN_API_KEY}"
KEY="${2:?usage: ./smoke.sh https://api.example.com ADMIN_API_KEY}"
BASE="${BASE%/}"

pass=0; fail=0
check() {
  local name="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    printf '  \033[32mPASS\033[0m  %-46s %s\n' "$name" "$actual"; pass=$((pass+1))
  else
    printf '  \033[31mFAIL\033[0m  %-46s got %s, want %s\n' "$name" "$actual" "$expected"; fail=$((fail+1))
  fi
}

echo "Smoke test → $BASE"
echo

# TLS must be valid, not merely present: Razorpay will not deliver webhooks to a host
# with a self-signed or expired certificate, and it fails silently from our side.
#
# Only meaningful over https. Asserted separately rather than folded into the /live
# check, because curl succeeds on plain http too — which would report a confident
# green for a box that can never receive a webhook.
case "$BASE" in
  https://*)
    if curl -fsS --max-time 15 "$BASE/live" >/dev/null 2>&1; then
      printf '  \033[32mPASS\033[0m  %-46s valid chain\n' "TLS certificate"; pass=$((pass+1))
    else
      printf '  \033[31mFAIL\033[0m  %-46s rejected by curl\n' "TLS certificate"; fail=$((fail+1))
    fi
    ;;
  *)
    printf '  \033[33mWARN\033[0m  %-46s not https — Razorpay cannot deliver here\n' "TLS certificate"
    ;;
esac

check "liveness /live" 200 "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/live")"

# /health also checks the database, so this is the DATABASE_URL and migration check.
health=$(curl -s --max-time 20 "$BASE/health")
check "readiness /health" 200 "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/health")"
echo "        $health"

# Unauthenticated reads must not work. This endpoint exposes customer names, email
# addresses and amounts owed.
check "dashboard is gated (no key)" 401 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/api/dashboard/queue")"

check "dashboard with admin key" 200 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "X-Admin-Key: $KEY" "$BASE/api/dashboard/queue")"

check "open disputes endpoint" 200 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "X-Admin-Key: $KEY" "$BASE/api/dashboard/disputes")"

# An unsigned webhook must be refused. If this returns 200, the signature check is not
# running and anyone can mark invoices paid.
# 400, not 401: the request never reaches auth. app/api/webhooks.py rejects it at the
# signature check, which is the correct and earlier failure.
check "webhook rejects unsigned payload" 400 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST \
      -H 'Content-Type: application/json' -d '{"event":"payment_link.paid"}' \
      "$BASE/api/webhooks/razorpay")"

# Dry run: evaluates the whole cadence and sends nothing. Proves the scheduler's work
# actually executes against this database, without contacting a single customer.
cycle=$(curl -s --max-time 90 -X POST -H "X-Admin-Key: $KEY" "$BASE/api/admin/run-cycle?dry_run=true")
if echo "$cycle" | grep -q '"considered"'; then
  printf '  \033[32mPASS\033[0m  %-46s %s\n' "dry-run recovery cycle" "$(echo "$cycle" | head -c 90)"; pass=$((pass+1))
else
  printf '  \033[31mFAIL\033[0m  %-46s %s\n' "dry-run recovery cycle" "$(echo "$cycle" | head -c 90)"; fail=$((fail+1))
fi

echo
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
