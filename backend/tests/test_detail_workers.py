from contextlib import contextmanager
from time import sleep

from app.detail_workers import DetailTask, DetailWorkerPool


def test_detail_workers_return_results_as_soon_as_each_read_finishes():
    closed = []

    @contextmanager
    def reader_factory():
        try:
            def read(row, known_price):
                sleep(row["delay"])
                return {"id": row["id"], "known_price": known_price}

            yield read
        finally:
            closed.append(True)

    tasks = [
        DetailTask("slow", "encar", {"id": "slow", "delay": 0.04}, 10),
        DetailTask("fast", "encar", {"id": "fast", "delay": 0}, 20),
    ]
    with DetailWorkerPool(
        reader_factory, workers=2, min_start_interval_seconds=0
    ) as pool:
        pool.submit_all(tasks)
        results = list(pool.results(len(tasks)))

    assert [result.task.key for result in results] == ["fast", "slow"]
    assert results[0].detail == {"id": "fast", "known_price": 20}
    assert len(closed) == 2


def test_detail_workers_return_reader_errors_without_stopping_other_tasks():
    @contextmanager
    def reader_factory():
        def read(row, _known_price):
            if row["id"] == "bad":
                raise ValueError("bad page")
            return row

        yield read

    tasks = [
        DetailTask("bad", "encar", {"id": "bad"}, None),
        DetailTask("good", "encar", {"id": "good"}, None),
    ]
    with DetailWorkerPool(reader_factory, workers=1) as pool:
        pool.submit_all(tasks)
        results = list(pool.results(len(tasks)))

    assert results[0].error and str(results[0].error) == "bad page"
    assert results[1].detail == {"id": "good"}
