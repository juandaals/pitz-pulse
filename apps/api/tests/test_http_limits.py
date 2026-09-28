"""Worker threads and body size: the busy bound holds at every allowed concurrency."""

import anyio.to_thread
from api_support import HEADERS, make_client
from fakes import FakeAdapter


async def _thread_tokens() -> float:
    return anyio.to_thread.current_default_thread_limiter().total_tokens


def test_thread_limiter_covers_three_times_the_max_concurrency(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([]), LLM_CONCURRENCY="16")
    with client:
        assert client.portal.call(_thread_tokens) >= 64


def test_thread_limiter_never_shrinks_below_the_default(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([]))
    with client:
        assert client.portal.call(_thread_tokens) >= 40


def test_oversized_body_is_413_before_auth(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([]))
    response = client.post(
        "/solicitudes", content=b"x" * 65537, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"
    assert response.json()["detail"]


def test_body_at_the_limit_reaches_auth(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([]))
    head, tail = b'{"id": "a", "message": "', b'"}'
    body = head + b"x" * (65536 - len(head) - len(tail)) + tail
    assert len(body) == 65536
    response = client.post(
        "/solicitudes", content=body, headers={"Content-Type": "application/json"}
    )
    assert (response.status_code, response.json()["error"]) == (401, "unauthorized")


def test_oversized_body_with_a_key_is_still_413(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([]))
    response = client.post(
        "/solicitudes",
        content=b"x" * 70000,
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 413
