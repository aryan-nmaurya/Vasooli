"""The models and the migrations must describe the same database.

Two incidents in one day came from these drifting apart, and both stayed silent until
something unrelated crashed:

* A model gained `invoices.reply_token` with no migration. Every dashboard query died
  with `UndefinedColumn`, which the frontend surfaced as "Cannot reach the backend" —
  sending the search in entirely the wrong direction.
* A migration that had already been applied was later *edited* to add three columns to
  `sending_domains`. Alembic will not re-run a revision it has already recorded, so
  the version table read `head` while the columns were absent.

Ordinary tests do not catch either: they only fail once code happens to touch the
missing column, which can be long after the change that caused it. Alembic can answer
the question directly — autogenerate compares the models against a live schema and
reports what is still needed.
"""

import pytest
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from alembic.operations.ops import AddColumnOp, CreateTableOp, ModifyTableOps
from sqlmodel import SQLModel

import app.models  # noqa: F401 — importing registers every table on SQLModel.metadata
from app.core.db import engine


def test_no_model_column_is_missing_from_the_database():
    """Anything the models declare must already exist in the schema.

    Deliberately one-directional. Only `AddColumnOp` and `CreateTableOp` count as
    drift — they mean the models expect something the database does not have, which
    is exactly the crash both incidents produced.

    The reverse is not asserted. Autogenerate cannot see objects created by raw SQL,
    and this schema has many: RLS policies, the append-only audit trigger, partial
    unique indexes. It reports those as spurious drops, and failing on them would make
    this test noisy enough that someone would rightly delete it.
    """
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": False})
        script = produce_migrations(context, SQLModel.metadata)

    missing: list[str] = []
    for op in script.upgrade_ops.ops:
        if isinstance(op, CreateTableOp):
            missing.append(f"table {op.table_name} does not exist")
        elif isinstance(op, ModifyTableOps):
            missing.extend(
                f"{op.table_name}.{child.column.name} does not exist"
                for child in op.ops
                if isinstance(child, AddColumnOp)
            )

    assert not missing, (
        "The models describe columns the database does not have. Add a NEW Alembic "
        "revision — never edit one that has already been applied, because Alembic will "
        "not re-run a revision it has already recorded:\n  " + "\n  ".join(sorted(missing))
    )


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("invoices", "reply_token"),
        ("sending_domains", "provider"),
        ("sending_domains", "local_part"),
    ],
)
def test_the_columns_that_have_already_drifted_once_are_present(table, column):
    """Named pins for the two incidents above.

    The general check covers these, but naming them keeps the history legible: a
    failure here says immediately which incident is repeating.
    """
    from sqlalchemy import inspect

    columns = {c["name"] for c in inspect(engine).get_columns(table)}
    assert column in columns, f"{table}.{column} is missing; a migration did not run"
