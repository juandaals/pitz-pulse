import threading
from datetime import UTC, datetime, timedelta

from pitz_pulse import db
from pitz_pulse.classifier import build_classifier
from pitz_pulse.config import parse_llm_settings
from pitz_pulse.providers.base import LLMError
from pitz_pulse.service import TriageService


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += timedelta(seconds=seconds)


def make_service(tmp_path, adapter, *, concurrency=4, clock=None, **overrides):
    settings = parse_llm_settings({"LLM_PROVIDER": "mock", "LLM_CONCURRENCY": str(concurrency)})
    path = tmp_path / "t.db"
    conn = db.connect(path)
    db.migrate(conn)
    conn.close()
    options = {"threshold": 0.7, "pending_stale_s": 530, "queue_wait_s": 0.2}
    options.update(overrides)
    return TriageService(
        path,
        build_classifier(settings, adapter),
        clock=clock or FakeClock(),
        complete_backoff_s=(0.0, 0.0, 0.0),
        **options,
    )


def unavailable():
    return LLMError("unavailable", "APIConnectionError")


def start(fn, *args):
    """Run fn(*args) in a thread; the returned dict gets "result" or "error" once joined."""
    outcome = {}

    def run():
        try:
            outcome["result"] = fn(*args)
        except Exception as exc:  # the test asserts on it
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome
