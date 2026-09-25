"""Bounded, cross-platform workers for independent listing detail reads."""

from __future__ import annotations

from contextlib import AbstractContextManager
from collections import deque
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class DetailTask:
    """Data safe to pass to a browser thread.

    ``context`` belongs to the coordinator and is never inspected by a worker.
    It lets the coordinator reconnect an out-of-order result to its project
    observation without sharing ORM objects between threads.
    """

    key: str
    source_key: str
    row: dict[str, Any]
    known_price: int | None
    context: Any = None
    read_kind: str = "FULL"


@dataclass(frozen=True)
class DetailResult:
    task: DetailTask
    detail: dict[str, Any] | None
    error: Exception | None
    elapsed_seconds: float


DetailReader = Callable[[dict[str, Any], int | None], dict[str, Any]]
DetailReaderFactory = Callable[[], AbstractContextManager[DetailReader]]


class DetailWorkerPool:
    """Run a finite group of independent detail reads with bounded concurrency.

    The source supplies a factory that owns its browser/session.  Workers only
    read pages; all database, report and notification work stays with the
    coordinator consuming ``results``.
    """

    _STOP = object()

    def __init__(
        self,
        reader_factory: DetailReaderFactory,
        workers: int = 1,
        min_start_interval_seconds: float = 0.5,
    ):
        self._reader_factory = reader_factory
        self._workers = max(1, min(int(workers), 2))
        self._min_start_interval_seconds = max(0, min_start_interval_seconds)
        self._stop_event = Event()
        self._start_event = Event()
        self._start_lock = Lock()
        self._next_start_at = 0.0
        self._tasks: Queue[DetailTask | object] = Queue(maxsize=self._workers * 3)
        self._results: Queue[DetailResult] = Queue()
        self._ready: Queue[Exception | None] = Queue()
        self._threads: list[Thread] = []
        self._pending: deque[DetailTask] = deque()

    def __enter__(self) -> "DetailWorkerPool":
        for index in range(self._workers):
            thread = Thread(
                target=self._run_worker,
                name=f"searchcar-detail-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

        failures: list[Exception] = []
        ready = 0
        while ready < self._workers:
            try:
                error = self._ready.get(timeout=20)
            except Empty as exc:
                self.close()
                raise RuntimeError("detail_worker_start_timeout") from exc
            ready += 1
            if error is not None:
                failures.append(error)
        if len(failures) == self._workers:
            self.close()
            raise failures[0]
        self._start_event.set()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def submit_all(self, tasks: list[DetailTask]) -> None:
        self._pending = deque(tasks)
        self._fill_task_queue()

    def results(self, count: int) -> Iterator[DetailResult]:
        for _ in range(count):
            if self._stop_event.is_set():
                return
            result = self._results.get()
            self._fill_task_queue()
            yield result

    def cancel(self) -> None:
        self._stop_event.set()

    def close(self) -> None:
        self._stop_event.set()
        self._start_event.set()
        for _ in self._threads:
            try:
                self._tasks.put_nowait(self._STOP)
            except Full:
                break
        for thread in self._threads:
            thread.join(timeout=2)

    def _run_worker(self) -> None:
        try:
            with self._reader_factory() as read:
                self._ready.put(None)
                self._start_event.wait()
                while not self._stop_event.is_set():
                    task = self._tasks.get()
                    if task is self._STOP:
                        return
                    assert isinstance(task, DetailTask)
                    if not self._wait_for_source_turn():
                        return
                    started = monotonic()
                    try:
                        detail = read(task.row, task.known_price)
                        result = DetailResult(
                            task=task,
                            detail=detail,
                            error=None,
                            elapsed_seconds=monotonic() - started,
                        )
                    except Exception as exc:  # Returned to coordinator for classification.
                        result = DetailResult(
                            task=task,
                            detail=None,
                            error=exc,
                            elapsed_seconds=monotonic() - started,
                        )
                    self._results.put(result)
        except Exception as exc:
            self._ready.put(exc)

    def _fill_task_queue(self) -> None:
        while self._pending and not self._stop_event.is_set():
            try:
                self._tasks.put_nowait(self._pending[0])
            except Full:
                return
            self._pending.popleft()

    def _wait_for_source_turn(self) -> bool:
        with self._start_lock:
            now = monotonic()
            delay = max(0.0, self._next_start_at - now)
            self._next_start_at = max(now, self._next_start_at) + (
                self._min_start_interval_seconds
            )
        return not self._stop_event.wait(delay)
