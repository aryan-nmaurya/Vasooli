"""Rollback order is a foreign-key dependency graph, not cosmetic formatting."""

from pathlib import Path


def test_identity_downgrade_drops_permission_children_before_parents():
    migration = (
        Path(__file__).parents[2] / "alembic/versions/e31f6a9c7d42_add_live_tenancy_and_identity.py"
    ).read_text()
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    drop_loop = downgrade.split("for table in (", 2)[2].split("):", 1)[0]

    assert drop_loop.index('"role_permissions"') < drop_loop.index('"permissions"')
    assert drop_loop.index('"user_permission_overrides"') < drop_loop.index('"permissions"')
    assert drop_loop.index('"merchant_memberships"') < drop_loop.index('"roles"')
    assert drop_loop.index('"merchant_invitations"') < drop_loop.index('"roles"')
    assert drop_loop.index('"mfa_factors"') < drop_loop.index('"users"')
