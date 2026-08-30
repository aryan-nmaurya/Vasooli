"""Daily comparison of Razorpay subscription state with the local ledger."""

from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from app.core.clock import utcnow
from app.integrations.razorpay_client import RazorpayClient, RazorpayError, get_razorpay_client
from app.models import BillingReconciliationRun, BillingSubscription


def reconcile_billing(
    session: Session,
    *,
    client: RazorpayClient | None = None,
    provider_snapshot: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int | str]:
    """Compare every provider subscription and record drift without mutating state.

    ``provider_snapshot`` is used by deterministic tests and staging replay. In a
    live deployment the snapshot is fetched from Razorpay using the platform client.
    Entitlements only change through signed webhooks; reconciliation produces an
    auditable alert rather than trusting an unsigned read to change billing state.
    """
    # This worker intentionally reads every merchant's subscription. The setting is
    # transaction-local and the policy still forbids tenant-owned writes without an
    # explicit merchant context.
    session.exec(text("SELECT set_config('app.service_role', 'true', true)"))
    run = BillingReconciliationRun(status="running")
    session.add(run)
    session.flush()
    drift: list[dict[str, Any]] = []
    rows = session.exec(
        select(BillingSubscription).where(BillingSubscription.razorpay_subscription_id.is_not(None))
    ).all()
    try:
        for row in rows:
            provider_id = str(row.razorpay_subscription_id)
            if provider_snapshot is not None:
                remote = provider_snapshot.get(provider_id)
            else:
                remote = (client or get_razorpay_client()).fetch_subscription(provider_id)
            run.checked_count += 1
            if not remote:
                drift.append({"subscription_id": provider_id, "reason": "missing_provider_state"})
                continue
            remote_status = str(remote.get("status") or "").lower()
            if (
                remote_status
                and remote_status != row.status
                and not {
                    remote_status,
                    row.status,
                }.issubset({"created", "authenticated"})
            ):
                drift.append(
                    {
                        "subscription_id": provider_id,
                        "local_status": row.status,
                        "provider_status": remote_status,
                    }
                )
        run.drift_count = len(drift)
        run.detail = {"drift": drift[:100], "checked_at": utcnow().isoformat()}
        run.status = "drift" if drift else "completed"
    except (RazorpayError, RuntimeError, ValueError) as exc:
        run.status = "failed"
        run.error = str(exc)[:1000]
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
    return {"status": run.status, "checked": run.checked_count, "drift": run.drift_count}
