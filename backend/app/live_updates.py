"""Small in-process fan-out for local desktop scan updates."""

from queue import Empty, Full, Queue
from threading import Lock


def _replace_latest(subscriber, event) -> None:
    # All producers hold the broker lock; consumers can only free queue space.
    try:
        subscriber.put_nowait(event)
    except Full:
        try:
            subscriber.get_nowait()
        except Empty:
            pass
        subscriber.put_nowait(event)


class LiveUpdateBroker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: dict[int, set[Queue[dict[str, int | str] | None]]] = {}
        self._closed = False

    def subscribe(self, owner_id: int) -> Queue[dict[str, int | str] | None]:
        subscriber: Queue[dict[str, int | str] | None] = Queue(maxsize=1)
        with self._lock:
            if self._closed:
                subscriber.put_nowait(None)
            else:
                self._subscribers.setdefault(owner_id, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Queue[dict[str, int | str] | None]) -> None:
        with self._lock:
            for owner_id, subscribers in list(self._subscribers.items()):
                subscribers.discard(subscriber)
                if not subscribers:
                    del self._subscribers[owner_id]

    def publish(self, *, owner_id: int, scan_id: int) -> None:
        with self._lock:
            for subscriber in self._subscribers.get(owner_id, ()):
                # Only invalidates the cache; the next API read is authoritative.
                _replace_latest(subscriber, {"type": "scan_changed", "scan_id": scan_id})

    def close(self) -> None:
        """Wake SSE readers, including full queues, before HTTP server shutdown."""
        with self._lock:
            self._closed = True
            for subscribers in self._subscribers.values():
                for subscriber in subscribers:
                    _replace_latest(subscriber, None)
            self._subscribers.clear()


live_updates = LiveUpdateBroker()


def publish_scan_update(owner_id: int, scan_id: int) -> None:
    live_updates.publish(owner_id=owner_id, scan_id=scan_id)
