"""External dead-man heartbeat contract."""

from app.scheduler import jobs


class _Response:
    def __init__(self) -> None:
        self.checked = False

    def raise_for_status(self) -> None:
        self.checked = True


def test_heartbeat_uses_a_bounded_request_and_checks_status(monkeypatch):
    response = _Response()
    seen: dict[str, object] = {}

    def fake_get(url: str, *, timeout: float):
        seen.update(url=url, timeout=timeout)
        return response

    monkeypatch.setattr(jobs.httpx, "get", fake_get)
    jobs._heartbeat("https://monitor.invalid/secret", check="service")

    assert seen == {"url": "https://monitor.invalid/secret", "timeout": 5.0}
    assert response.checked is True


def test_empty_heartbeat_url_does_not_make_a_request(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("network request should not be attempted")

    monkeypatch.setattr(jobs.httpx, "get", unexpected)
    jobs._heartbeat("", check="service")
