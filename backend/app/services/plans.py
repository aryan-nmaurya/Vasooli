"""The plan catalogue: one source of truth for price, limits and features.

The public pricing page and the in-product billing section must agree, and the
enforcement layer must agree with both. Three copies of the same numbers drift, and
the drift is invisible until a merchant is charged for a plan whose limits the server
never applied. So the numbers live here, the API serves them from here, and the
frontend renders whatever the API returns rather than repeating them.

`Feature` values are the gate names used by `assert_feature_entitled`. Adding a
feature to a plan is a data change here, never a conditional at the call site.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Feature(StrEnum):
    """Capabilities a plan may grant.

    Deliberately absent: reading and exporting the merchant's own data. That is
    theirs regardless of plan or payment state — see `billing.write_paused_reason`.
    """

    ZOHO_INTEGRATION = "zoho_integration"
    CUSTOM_POLICIES = "custom_policies"
    ROLE_BASED_ACCESS = "role_based_access"
    EXCEPTIONS_QUEUE = "exceptions_queue"
    BILLING_RECONCILIATION = "billing_reconciliation"


@dataclass(frozen=True)
class Plan:
    slug: str
    name: str
    amount_paise: int
    included_active_invoices: int
    #: Seats are people who can sign in to the workspace, the owner included.
    included_seats: int
    description: str
    #: Marketing copy, rendered verbatim by the pricing page and billing section.
    highlights: tuple[str, ...]
    features: frozenset[Feature] = field(default_factory=frozenset)

    @property
    def amount_inr(self) -> int:
        return self.amount_paise // 100


STARTER = Plan(
    slug="starter",
    name="Starter",
    amount_paise=199_900,
    included_active_invoices=100,
    included_seats=1,
    description="For lean finance teams replacing manual follow-up.",
    highlights=(
        "Policy-controlled recovery",
        "CSV ledger import",
        "Payment links and reconciliation",
        "Promises and disputes",
        "Complete audit log",
        "Email support",
    ),
    features=frozenset(),
)

GROWTH = Plan(
    slug="growth",
    name="Growth",
    amount_paise=599_900,
    included_active_invoices=500,
    included_seats=5,
    description="For growing teams that need connected, repeatable collections.",
    highlights=(
        "Everything in Starter",
        "Zoho Books integration",
        "Custom recovery policies",
        "Role-based access",
        "Operational exceptions queue",
        "Priority support",
    ),
    features=frozenset(
        {
            Feature.ZOHO_INTEGRATION,
            Feature.CUSTOM_POLICIES,
            Feature.ROLE_BASED_ACCESS,
            Feature.EXCEPTIONS_QUEUE,
        }
    ),
)

SCALE = Plan(
    slug="scale",
    name="Scale",
    amount_paise=1_499_900,
    included_active_invoices=2_000,
    included_seats=15,
    description="For high-volume operations with deeper controls and oversight.",
    highlights=(
        "Everything in Growth",
        "Advanced team controls",
        "Audit and ledger exports",
        "Billing reconciliation",
        "Onboarding support",
    ),
    features=frozenset(
        {
            Feature.ZOHO_INTEGRATION,
            Feature.CUSTOM_POLICIES,
            Feature.ROLE_BASED_ACCESS,
            Feature.EXCEPTIONS_QUEUE,
            Feature.BILLING_RECONCILIATION,
        }
    ),
)

#: Ordered cheapest first. Order is meaningful: the billing UI renders it, and
#: `is_upgrade_from` compares positions.
PLANS: tuple[Plan, ...] = (STARTER, GROWTH, SCALE)

PLANS_BY_SLUG: dict[str, Plan] = {plan.slug: plan for plan in PLANS}

#: The plan a trial grants. Named rather than indexed so the trial tier can move.
TRIAL_PLAN = STARTER


def plan_for(slug: str) -> Plan:
    plan = PLANS_BY_SLUG.get(slug)
    if plan is None:
        raise KeyError(f"Unknown plan {slug!r}")
    return plan


def plan_rank(slug: str) -> int:
    """Position in the catalogue, used to tell an upgrade from a downgrade."""
    for index, plan in enumerate(PLANS):
        if plan.slug == slug:
            return index
    raise KeyError(f"Unknown plan {slug!r}")
