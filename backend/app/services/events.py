"""Best-effort in-process event fanout for dashboard SSE clients."""

from collections import deque
from threading import Lock
from typing import Any

_events: deque[tuple[int, dict[str, Any]]] = deque(maxlen=256)
_lock = Lock()
_next_id = 0


def publish(event: dict[str, Any]) -> None:
    global _next_id
    with _lock:
        _next_id += 1
        _events.append((_next_id, event))


def after_id(last_id: int) -> list[tuple[int, dict[str, Any]]]:
    with _lock:
        return [(event_id, event) for event_id, event in _events if event_id > last_id]
