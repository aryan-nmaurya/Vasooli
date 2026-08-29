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
        elif isinstance(node, ast.ImportFrom):
            # Relative imports were skipped entirely by an earlier `node.level == 0`
            # guard, which meant `from ..integrations.razorpay_client import ...`
            # inside app/ai passed this test. Resolve the level against the module's
            # own package so a relative import is checked exactly like an absolute one.
            if node.level == 0:
                module = node.module or ""
            else:
                package = path.relative_to(APP.parent).with_suffix("").parts
                # `x.y.__init__` addresses package `x.y`; anything else drops its own
                # module name before walking up one package per extra dot.
                base = list(package[:-1] if package[-1] == "__init__" else package[:-1])
                up = node.level - 1
                base = base[: len(base) - up] if up else base
                module = ".".join([*base, node.module] if node.module else base)
            if not module:
                continue
            found.add(module)
            found.update(f"{module}.{a.name}" for a in node.names)
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
            # Named integrations.
            "app.integrations",
            "app.services",
            "app.core.db",
            "app.api",
            "resend",
            "sendgrid",
            "razorpay",
            # Raw capability. Banning only the project's own wrappers left the
            # underlying tools reachable: app.ai already imports `settings`, which
            # carries the database URL and every provider credential, so a bare
            # `create_engine` or `httpx.post` was all it took to reach the things
            # this test claims are unreachable.
            "sqlmodel",
            "sqlalchemy",
            "psycopg",
            "httpx",
            "requests",
            "urllib.request",
            "smtplib",
            "socket",
            "subprocess",
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

    #: security.py is exempt, and must stay exempt. Session expiry has to run on real
    #: wall time: app.core.clock carries DEMO_TIME_OFFSET_DAYS, so a session checked
    #: against it would be silently extended when the demo clock is wound forward, and
    #: expired the moment it is wound back. Authentication lifetimes are not part of
    #: the business timeline.
    exempt = {"clock.py", "security.py"}

    offenders = []
    for path in APP.rglob("*.py"):
        if path.name in exempt:
            continue
        source = path.read_text()
        for pattern in banned:
            if pattern + "(" in source:
                offenders.append(f"{path.relative_to(APP.parent)} uses {pattern}()")
    assert not offenders, "Read time via app.core.clock:\n  " + "\n  ".join(offenders)


def test_no_floats_on_money_paths():
    """Money is integer paise. A float balance is an invoice that never closes.

    Checked by field name rather than by an allowlist of files, because the rule is
    about what a value MEANS, not where it lives. `reason_confidence: float` is fine —
    a confidence is not money — while `amount_paise: float` anywhere would be a defect,
    including in a file that happens to be allowlisted today.
    """
    money_words = ("paise", "amount", "price", "rupee", "balance", "total_paid")
    offenders = []

    for path in APP.rglob("*.py"):
        if path.name == "money.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            # Annotated field whose name reads as money, typed float.
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id.lower()
                if any(w in name for w in money_words) and "float" in ast.unparse(node.annotation):
                    offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno} {name}")
            # A bare float() cast anywhere in app code.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "float"
            ):
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno} float() cast")

    assert not offenders, "floats on money paths:\n  " + "\n  ".join(offenders)


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
