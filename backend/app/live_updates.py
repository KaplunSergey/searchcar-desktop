"""Small in-process fan-out for local desktop scan updates."""

from queue import Empty, Full, Queue
from threading import Lock


class LiveUpdateBroker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: dict[int, set[Queue[dict[str, int | str]]]] = {}

    def subscribe(self, owner_id: int) -> Queue[dict[str, int | str]]:
        subscriber: Queue[dict[str, int | str]] = Queue(maxsize=1)
        with self._lock:
            self._subscribers.setdefault(owner_id, set()).add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Queue[dict[str, int | str]]) -> None:
        with self._lock:
            for owner_id, subscribers in list(self._subscribers.items()):
                subscribers.discard(subscriber)
                if not subscribers:
                    del self._subscribers[owner_id]

    def publish(self, *, owner_id: int, scan_id: int) -> None:
        with self._lock:
            subscribers = tuple(self._subscribers.get(owner_id, ()))
        event = {"type": "scan_changed", "scan_id": scan_id}
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except Full:
                # The event only invalidates the scan cache. Keeping the latest
                # one is enough; the next API read remains the source of truth.
                try:
                    subscriber.get_nowait()
                except Empty:
                    continue
                try:
                    subscriber.put_nowait(event)
                except Full:
                    continue


live_updates = LiveUpdateBroker()


def publish_scan_update(owner_id: int, scan_id: int) -> None:
    live_updates.publish(owner_id=owner_id, scan_id=scan_id)
