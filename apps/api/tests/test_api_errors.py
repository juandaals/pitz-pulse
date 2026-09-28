import sqlite3
import threading

import pytest
from api_support import HEADERS, make_client
from fakes import FakeAdapter, GateAdapter, make_call

from pitz_pulse import repository
from pitz_pulse.providers.base import LLMError

BODY = {"id": "MSG-01", "message": "Error 500 al subir catálogo"}


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, FakeAdapter([make_call() for _ in range(5)]))[0]


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": "wrong"}, {"X-API-Key": "clé".encode()}, {"X-API-Key": b"\xff"}],
)
def test_missing_wrong_or_non_ascii_key_is_401(client, headers):
    response = client.get("/solicitudes", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_without_a_key_an_invalid_body_is_401_and_bad_json_is_422(client):
    invalid = client.post("/solicitudes", json={"id": "bad id"})
    assert (invalid.status_code, invalid.json()["error"]) == (401, "unauthorized")
    broken = client.post(
        "/solicitudes", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert (broken.status_code, broken.json()["error"]) == (422, "validation_error")


def test_post_conflict_and_validation_codes(client):
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    conflict = client.post("/solicitudes", json={**BODY, "message": "otro"}, headers=HEADERS)
    assert (conflict.status_code, conflict.json()["error"]) == (409, "id_conflict")
    invalid = client.post("/solicitudes", json={"id": "x", "message": ""}, headers=HEADERS)
    assert (invalid.status_code, invalid.json()["error"]) == (422, "validation_error")
    assert invalid.json()["fields"]
    extra = client.post("/solicitudes", json={**BODY, "id": "y", "urgent": True}, headers=HEADERS)
    assert extra.status_code == 422


def test_lone_surrogate_is_422_not_500(client):
    raw = '{"id": "s1", "message": "hola \\ud800"}'
    response = client.post(
        "/solicitudes", content=raw, headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert response.status_code == 422


def test_classification_failure_is_502_with_kind(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([LLMError("rejected", "APIStatusError:401")]))
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert response.status_code == 502
    assert response.json() == {
        "error": "classification_failed",
        "detail": response.json()["detail"],
        "kind": "llm_rejected",
    }


def test_unexpected_error_is_500_with_a_constant_body(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([RuntimeError("SENTINEL")]))
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "detail": "internal error"}


def test_busy_is_503_with_retry_after(tmp_path):
    gate = GateAdapter([make_call(), make_call()])
    client, app = make_client(tmp_path, gate, LLM_CONCURRENCY="1")
    app.state.service.queue_wait_s = 0.1
    worker = threading.Thread(
        target=client.post,
        args=("/solicitudes",),
        kwargs={"json": {"id": "a", "message": "x"}, "headers": HEADERS},
    )
    worker.start()
    assert gate.entered.acquire(timeout=5)
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert (response.status_code, response.json()["error"]) == (503, "busy")
    assert response.headers["Retry-After"] == "30"
    health = client.get("/health")  # async route: answers while the only slot is taken
    assert health.status_code == 200
    gate.release.set()
    worker.join()


def test_locked_database_is_503_db_busy_with_retry_after(client, monkeypatch):
    def locked(self, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository.Repository, "get", locked)
    response = client.get("/solicitudes/MSG-01", headers=HEADERS)
    assert (response.status_code, response.json()["error"]) == (503, "db_busy")
    assert response.headers["Retry-After"] == "1"


def test_patch_on_failed_row_is_409(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([LLMError("unavailable", "APIConnectionError")]))
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    response = client.patch(
        "/solicitudes/MSG-01", json={"categoria": "datos", "author": "a"}, headers=HEADERS
    )
    assert (response.status_code, response.json()["error"]) == (409, "not_classified")


def test_unknown_route_and_method_use_the_error_body(client):
    missing = client.get("/nope")
    assert (missing.status_code, missing.json()["error"]) == (404, "not_found")
    method = client.delete("/solicitudes/MSG-01", headers=HEADERS)
    assert (method.status_code, method.json()["error"]) == (405, "method_not_allowed")
