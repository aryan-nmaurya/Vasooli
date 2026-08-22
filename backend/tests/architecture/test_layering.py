"""Architecture tests — the layering rules, enforced instead of documented.

Vasooli's central claim is that the language model reasons while deterministic code
decides anything touching money, contact frequency, or compliance (Doc §5). A comment
saying so is worth little; an import graph that makes the violation impossible is
worth a lot. These tests parse the AST of every module and fail the build when a layer
reaches somewhere it should not.

Each rule below corresponds to an exit criterion in the implementation plan.
"""

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _modules(package: str) -> list[pathlib.Path]:
    return sorted((APP / package).rglob("*.py"))


def _imports(path: pathlib.Path) -> set[str]:
    """Every module name imported by `path`, normalized to dotted paths."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def _assert_forbidden(package: str, forbidden: tuple[str, ...], why: str) -> None:
    violations = [
        f"{path.relative_to(APP.parent)} imports {imported}"
        for path in _modules(package)
        for imported in _imports(path)
        if any(imported == f or imported.startswith(f + ".") for f in forbidden)
    ]
    assert not violations, f"{why}\n  " + "\n  ".join(violations)


# --------------------------------------------------------------------------------
# The AI layer cannot act. Plan Phase 6 exit criterion.
# --------------------------------------------------------------------------------


def test_ai_layer_cannot_send_or_move_money():
    """`app.ai` must not be able to reach an email sender, Razorpay, or a DB write.

    This is the structural form of "the LLM recommends, it does not execute". If this
    test ever fails, the project's main architectural claim has stopped being true.
    """
    _assert_forbidden(
        "ai",
        (
            "app.integrations.email",
            "app.integrations.razorpay_client",
            "app.services",
            "app.core.db",
            "app.api",
            "resend",
            "sendgrid",
            "razorpay",
        ),
        "app.ai must stay advisory: it may not send, persist, or move money.",
    )


# --------------------------------------------------------------------------------
# The policy layer is pure. Plan Phase 5 exit criterion.
# --------------------------------------------------------------------------------


def test_policy_layer_is_pure():
    """`app.policy` must be a pure function of its arguments.

    No DB, no network, no wall clock — `now` is injected. Purity is what makes the
    ~40 table-driven rule tests and the eval harness's simulated clock possible.
    """
    _assert_forbidden(
        "policy",
        (
            "app.core.db",
            "app.core.clock",
            "app.integrations",
            "app.services",
            "app.ai",
            "app.api",
            "app.scheduler",
            "httpx",
            "requests",
            "sqlalchemy",
            "sqlmodel",
        ),
        "app.policy must stay pure: no DB, no network, no clock. Pass `now` in.",
    )


# --------------------------------------------------------------------------------
# Dependencies point one way.
# --------------------------------------------------------------------------------


def test_integrations_do_not_depend_on_business_logic():
    """Integrations are leaves, so they stay mockable at the boundary."""
    _assert_forbidden(
        "integrations",
        ("app.services", "app.policy", "app.api", "app.scheduler"),
        "app.integrations must not import inward; dependencies point outward only.",
    )


def test_models_depend_only_on_core():
    """Entities are data. Rules live in app.policy, orchestration in app.services."""
    _assert_forbidden(
        "models",
        ("app.services", "app.policy", "app.ai", "app.integrations", "app.api"),
        "app.models must stay data-only.",
    )


def test_api_does_not_call_integrations_directly():
    """Routers stay thin: HTTP in, service call, DTO out.

    A router that calls Razorpay directly is a code path the eval harness and the
    scheduler will never exercise, which is exactly where untested bugs live.
    """
    offenders = [
        f"{path.relative_to(APP.parent)} imports {imported}"
        for path in _modules("api")
        # The webhook router is the documented exception: Razorpay signature
        # verification must run on the raw request body, before any other layer.
        if path.name != "webhooks.py"
        for imported in _imports(path)
        if imported.startswith("app.integrations")
    ]
    assert not offenders, (
        "app.api must reach external systems through app.services:\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------------
# Cross-cutting conventions.
# --------------------------------------------------------------------------------


def test_time_is_read_only_through_the_clock_module():
    """`DEMO_TIME_OFFSET_DAYS` only works if nothing bypasses app.core.clock.

    A single stray `datetime.now()` in the cadence path makes the demo fast-forward
    silently wrong, in a way that looks like a policy bug rather than a clock bug.
    """
    banned = ("datetime.now", "datetime.utcnow", "datetime.today", "date.today", "time.time")
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "clock.py":
            continue
        source = path.read_text()
        for pattern in banned:
            if pattern + "(" in source:
                offenders.append(f"{path.relative_to(APP.parent)} uses {pattern}()")
    assert not offenders, "Read time via app.core.clock:\n  " + "\n  ".join(offenders)


def test_no_floats_on_money_paths():
    """Money is integer paise. A float balance is an invoice that never closes."""
    offenders = [
        f"{path.relative_to(APP.parent)}"
        for path in APP.rglob("*.py")
        if path.name != "money.py"
        and ("float(" in path.read_text() or ": float" in path.read_text())
    ]
    # `reason_confidence: float` is legitimate — confidence is not money. Allowlist it
    # explicitly so the rule keeps biting everywhere else.
    allowed = {"app/models/invoice.py", "app/ai/schemas.py", "app/core/config.py"}
    unexpected = [o for o in offenders if o not in allowed]
    assert not unexpected, f"floats found outside allowlisted files: {unexpected}"


def test_schema_is_defined_by_migrations_not_create_all():
    """A create_all() call would let local and deployed schemas diverge silently.

    Matched on the AST rather than the text, so prose mentioning the ban (such as
    db.py's own docstring) does not trip the rule.
    """
    offenders = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_all"
            ):
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    assert not offenders, f"use Alembic, not create_all(): {offenders}"


@pytest.mark.parametrize(
    "package",
    ["core", "models", "schemas", "policy", "ai", "integrations", "services", "api", "scheduler"],
)
def test_every_layer_is_a_real_package(package):
    """Guards against a layer quietly disappearing or being renamed in a refactor."""
    assert (APP / package / "__init__.py").exists(), f"app/{package}/__init__.py is missing"
