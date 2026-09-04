"""No request handler may touch an ORM attribute after `session.commit()`.

This is the single most expensive bug in this codebase's history: the same shape has
reached production three separate times, in three different files, and cost a bare 500
on a real merchant route each time.

Why it happens. `set_merchant_context` uses `set_config(..., true)`, which is
transaction-local — that is what stops it riding a pooled connection into another
request. It therefore dies at `session.commit()`. `app.core.db.get_session` yields a
plain `Session`, so `expire_on_commit` is True and every attribute touched afterwards
re-SELECTs. Under the NOBYPASSRLS role production connects as, that query runs with no
tenant, the policy matches nothing, and SQLAlchemy raises `ObjectDeletedError`.

It is invisible to almost every test in this suite, because they connect as a superuser
and RLS does not apply.

Two safe shapes, and this test accepts both:

* wrap the commit and the reads in `merchant_scope(session, merchant_id)`, which
  re-applies the setting on every transaction the block opens; or
* copy the values into locals BEFORE committing, which is what routes without a
  LiveContext must do.
"""

import ast
import pathlib

API_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "api"

#: Attribute names that are safe to read on anything, because they are not ORM state.
SAFE_ATTRS = frozenset(
    {
        "value",  # StrEnum members
        "name",
        "status_code",
        "detail",
        "isoformat",
        "hex",
        "get",
        "append",
        "items",
        "keys",
        "values",
        "encode",
        "decode",
        "lower",
        "upper",
        "casefold",
        "strip",
        "split",
        "join",
        "format",
        "commit",
        "rollback",
        # `to_dict` is NOT here. It was, and that is how the billing cancel endpoint
        # shipped a bare 500: `SubscriptionState.to_dict()` looks like a plain
        # serializer but reaches into the BillingSubscription row it holds, so
        # calling it after commit re-SELECTs with no tenant. A method name cannot
        # tell you whether it touches ORM state; only the receiver can.
        "refresh",
        "add",
        "exec",
        "flush",
    }
)

#: Locals that are plainly not ORM instances.
SAFE_NAMES = frozenset(
    {"settings", "session", "request", "response", "payload", "self", "uuid", "log", "exc"}
)


def _commit_calls(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
    )


def _within_merchant_scope(fn: ast.FunctionDef, target: ast.AST) -> bool:
    """True when `target` sits inside a `with merchant_scope(...)` block in `fn`."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.With):
            continue
        uses_scope = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "merchant_scope"
            for item in node.items
        )
        if not uses_scope:
            continue
        if any(child is target for child in ast.walk(node)):
            return True
    return False


def _is_tenant_scoped(fn: ast.FunctionDef) -> bool:
    """True when the handler takes a LiveContext.

    That dependency is what calls `set_merchant_context`, so it is exactly the set of
    handlers where a transaction-local tenant exists to be lost at commit. The operator
    console (`auth`, `dashboard`, `admin`) and the webhook endpoints are excluded on
    purpose: their tables carry no row-level security, so a post-commit re-SELECT there
    returns the row rather than raising.
    """
    args = list(fn.args.args) + list(fn.args.kwonlyargs)
    for arg in args:
        annotation = ast.unparse(arg.annotation) if arg.annotation else ""
        if "LiveContext" in annotation:
            return True
        # Module-private helpers take the context positionally and unannotated
        # (`_create_request(session, request, context, ...)`), and the commit often
        # lives in one of those rather than in the route body. Matching the parameter
        # name as well is what makes the check reach them.
        if arg.arg == "context":
            return True
    return False


def _offenders_in(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_tenant_scoped(fn):
            continue
        body = list(fn.body)
        # Index of the first statement containing a commit, at the function's top level.
        commit_at = None
        for index, stmt in enumerate(body):
            if any(_commit_calls(sub) for sub in ast.walk(stmt)):
                commit_at = index
                break
        if commit_at is None:
            continue
        commit_stmt = body[commit_at]
        if _within_merchant_scope(fn, commit_stmt):
            continue
        for stmt in body[commit_at + 1 :]:
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Attribute):
                    continue
                if node.attr in SAFE_ATTRS:
                    continue
                # A read that is itself inside a `with merchant_scope(...)` is safe even
                # when the commit sat outside it — the setting is re-applied for the
                # transaction the read opens. `resolve_case` commits inside a branch and
                # returns from within the scope, which is exactly this shape.
                if _within_merchant_scope(fn, node):
                    continue
                if isinstance(node.value, ast.Name) and node.value.id not in SAFE_NAMES:
                    found.append(
                        f"{path.name}:{node.lineno} {fn.name}() reads "
                        f"`{node.value.id}.{node.attr}` after commit()"
                    )
    return found


def test_no_handler_reads_orm_state_after_commit():
    offenders: list[str] = []
    for path in sorted(API_DIR.glob("*.py")):
        offenders.extend(_offenders_in(path))
    assert not offenders, (
        "These reads re-SELECT with no tenant under the production role and raise "
        "ObjectDeletedError as a bare 500. Wrap the commit in `merchant_scope(...)`, "
        "or copy the values into locals before committing:\n  " + "\n  ".join(offenders)
    )
