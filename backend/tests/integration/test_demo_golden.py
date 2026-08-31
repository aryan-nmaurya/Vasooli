"""Merge-blocking regression gate for the frozen Vasooli demo."""

import os
from pathlib import Path

from app.core.config import settings
from tests.demo_golden import (
    capture_api_responses,
    capture_ledger,
    capture_policy_traces,
    pretty_json,
    seed_frozen_demo,
)

GOLDEN_DIR = Path(__file__).parents[1] / "golden" / "demo"
UPDATE_GOLDENS = os.environ.get("UPDATE_DEMO_GOLDENS") == "1"


def _assert_golden(name: str, actual: object) -> None:
    path = GOLDEN_DIR / name
    rendered = pretty_json(actual)
    if UPDATE_GOLDENS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    assert path.exists(), f"Missing demo golden {path}"
    assert rendered == path.read_text(encoding="utf-8"), (
        f"Frozen demo changed: {name}. If this is an intentional, reviewed demo change, "
        "re-baseline with UPDATE_DEMO_GOLDENS=1 and review the fixture diff."
    )


def test_frozen_demo_ledger(session):
    seed_frozen_demo(session)
    _assert_golden("ledger.json", capture_ledger(session))


def test_frozen_demo_policy_traces(session):
    seed_frozen_demo(session)
    _assert_golden("policy_traces.json", capture_policy_traces(session))


def test_frozen_demo_api_responses(session, client, admin_headers, monkeypatch):
    # The reviewer settings panel is part of the frozen demo. Enable it for the
    # baseline even though production-safe test defaults leave it off.
    monkeypatch.setattr(settings, "demo_controls_enabled", True)
    seed_frozen_demo(session)
    _assert_golden(
        "api_responses.json",
        capture_api_responses(client, session, admin_headers),
    )
