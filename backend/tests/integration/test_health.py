"""Health endpoint contract — Railway's healthcheck depends on this shape."""

from unittest.mock import patch


def test_health_reports_ok_with_live_db(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["version"]
    assert body["environment"] == "test"


def test_health_returns_503_when_db_unreachable(client):
    with patch("app.api.health.check_database", return_value=(False, "boom")):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["db"] == "unavailable"
    assert "boom" in resp.json()["detail"]


def test_live_never_touches_the_database(client):
    with patch("app.api.health.check_database", return_value=(False, "boom")):
        resp = client.get("/live")
    assert resp.status_code == 200


def test_request_id_is_echoed(client):
    resp = client.get("/live", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


def test_request_id_is_generated_when_absent(client):
    assert client.get("/live").headers.get("X-Request-ID")
