"""run_batch in isolation: sliding window, per-item failures and the credential stop rule."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pitz_pulse import batch_run
from pitz_pulse.batch_run import run_batch
from pitz_pulse.classifier import ClassificationError
from pitz_pulse.graph import AttemptRecord
from pitz_pulse.schema import RequestInput


class CountingPool(ThreadPoolExecutor):
    """Records the peak of submitted-but-unfinished futures and whether shutdown ran."""

    instances: list["CountingPool"] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lock = threading.Lock()
        self.outstanding = self.peak = 0
        self.shut_down = False
        CountingPool.instances.append(self)

    def submit(self, fn, *args, **kwargs):
        with self.lock:
            self.outstanding += 1
            self.peak = max(self.peak, self.outstanding)
        future = super().submit(fn, *args, **kwargs)
        future.add_done_callback(self._finished)
        return future

    def _finished(self, _future):
        with self.lock:
            self.outstanding -= 1

    def shutdown(self, *args, **kwargs):
        self.shut_down = True
        super().shutdown(*args, **kwargs)


@pytest.fixture
def pool(monkeypatch):
    CountingPool.instances.clear()
    monkeypatch.setattr(batch_run, "ThreadPoolExecutor", CountingPool)
    yield CountingPool.instances


class StubClassifier:
    """run_batch only needs .classify(request); each id maps to a behavior."""

    def __init__(self, behaviors):
        self.behaviors = behaviors

    def classify(self, request):
        return self.behaviors.get(request.id, lambda: request.id)()


def _requests(*ids):
    return [RequestInput(id=i, message="m") for i in ids]


def test_window_refills_while_a_slow_item_is_in_flight(pool):
    fast_done = threading.Event()
    finished = []

    def slow():
        released = fast_done.wait(timeout=2)
        return "slow" if released else "timed-out"

    def fast(name):
        def run():
            finished.append(name)
            if len(finished) == 4:
                fast_done.set()
            return name

        return run

    behaviors = {"S": slow, **{f"F{n}": fast(f"F{n}") for n in range(4)}}
    outcomes, failures = run_batch(
        StubClassifier(behaviors), _requests("S", "F0", "F1", "F2", "F3"), 2
    )
    assert failures == []
    assert outcomes["S"] == "slow"  # every fast item finished while the slow one was in flight
    assert pool[0].peak <= 2  # never more than `concurrency` submitted at once


def test_unexpected_exception_is_recorded_and_batch_continues(pool, caplog):
    def boom():
        raise ValueError("SECRETTEXT")

    outcomes, failures = run_batch(StubClassifier({"B": boom}), _requests("A", "B", "C"), 1)
    assert set(outcomes) == {"A", "C"}
    assert [(f.id, f.kind) for f in failures] == [("B", "unexpected")]
    assert pool[0].shut_down
    assert "SECRETTEXT" not in caplog.text


def _rejected(error_type):
    def run():
        raise ClassificationError(
            "llm_rejected", [AttemptRecord(1, "rejected", error_type=error_type)]
        )

    return run


@pytest.mark.parametrize(
    "error_type",
    [
        "APIStatusError:401",
        "APIStatusError:403",
        "api_error_status:401",
        "authentication_failed",
        "billing_error",
        "CLINotFoundError",
    ],
)
def test_credential_rejection_stops_the_batch(pool, error_type):
    behaviors = {"A": _rejected(error_type)}
    outcomes, failures = run_batch(StubClassifier(behaviors), _requests("A", "B", "C", "D"), 1)
    kinds = {f.id: f.kind for f in failures}
    assert kinds["A"] == "llm_rejected" and outcomes == {}
    assert sorted(i for i, k in kinds.items() if k == "cancelled") == ["B", "C", "D"]


@pytest.mark.parametrize(
    "error_type", ["APIStatusError:400", "APIStatusError:413", "invalid_request"]
)
def test_other_rejections_are_recorded_and_the_batch_continues(pool, error_type):
    behaviors = {"A": _rejected(error_type)}
    outcomes, failures = run_batch(StubClassifier(behaviors), _requests("A", "B", "C"), 1)
    assert set(outcomes) == {"B", "C"}
    assert [(f.id, f.kind) for f in failures] == [("A", "llm_rejected")]


def test_base_exception_propagates_and_pool_is_shut_down(pool):
    class Stop(BaseException):
        pass

    def stop():
        raise Stop

    with pytest.raises(Stop):
        run_batch(StubClassifier({"A": stop}), _requests("A"), 1)
    assert pool[0].shut_down
