import threading

import pytest
from api_support import HEADERS, make_client
from fakes import VALID_OUTPUT, FakeAdapter, GateAdapter, make_call

from pitz_pulse.providers.base import LLMError

BODY = {"id": "MSG-01", "message": "Error 500 al subir catálogo"}
ITEM_KEYS = {
    "id", "message", "source_area", "status", "categoria", "prioridad", "area_sugerida",
    "idioma", "resumen", "requiere_info", "pregunta_seguimiento", "confianza",
    "version_prompt", "provider", "model", "needs_review", "corrected", "error",
    "created_at", "updated_at",
}  # fmt: skip


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, FakeAdapter([make_call() for _ in range(5)]))[0]


def test_lowercase_api_key_header_is_accepted(client):
    assert client.get("/solicitudes", headers={"x-api-key": "test-key"}).status_code == 200


def test_open_endpoints_need_no_key(client):
    for path in ("/health", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 200


def test_health_reports_the_effective_adapter(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "provider": "fake",
        "model": "fake-model",
        "prompt_version": "v1",
    }


def test_post_201_then_200_with_item_keys(client):
    first = client.post("/solicitudes", json=BODY, headers=HEADERS)
    second = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert (first.status_code, second.status_code) == (201, 200)
    item = first.json()
    assert set(item) == ITEM_KEYS
    assert item["categoria"] == "bug" and item["requiere_info"] is True
    assert item["provider"] == "fake" and item["needs_review"] is False


def test_in_progress_is_409_with_retry_after(tmp_path):
    gate = GateAdapter([make_call()])
    client, _ = make_client(tmp_path, gate)
    worker = threading.Thread(
        target=client.post, args=("/solicitudes",), kwargs={"json": BODY, "headers": HEADERS}
    )
    worker.start()
    assert gate.entered.acquire(timeout=5)
    response = client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert (response.status_code, response.json()["error"]) == (409, "in_progress")
    assert int(response.headers["Retry-After"]) > 0
    gate.release.set()
    worker.join()


def test_list_filters_paging_and_unknown_params(client):
    for i in range(3):
        client.post("/solicitudes", json={"id": f"m{i}", "message": f"hola {i}"}, headers=HEADERS)
    page = client.get("/solicitudes?categoria=bug&limit=2", headers=HEADERS).json()
    assert (page["total"], len(page["items"]), page["limit"], page["offset"]) == (3, 2, 2, 0)
    assert client.get("/solicitudes?status=pending", headers=HEADERS).json()["total"] == 0
    assert client.get("/solicitudes?needs_review=false", headers=HEADERS).json()["total"] == 3
    for query in ("area=backend", "categoria=Bug", "limit=0", "limit=101", f"offset={2**31}"):
        response = client.get(f"/solicitudes?{query}", headers=HEADERS)
        assert (response.status_code, response.json()["error"]) == (422, "validation_error"), query
    unknown = client.get("/solicitudes?area=backend", headers=HEADERS).json()
    assert unknown["fields"][0]["loc"] == ["query", "area"]


def test_combined_filters_over_http(tmp_path):
    low_datos = make_call({**VALID_OUTPUT, "categoria": "datos", "confianza": 0.5})
    adapter = FakeAdapter([make_call(), low_datos, LLMError("unavailable", "APIConnectionError")])
    client, _ = make_client(tmp_path, adapter)
    for i in range(3):
        client.post("/solicitudes", json={"id": f"m{i}", "message": f"hola {i}"}, headers=HEADERS)

    def ids(query):
        page = client.get(f"/solicitudes?{query}", headers=HEADERS).json()
        return sorted(item["id"] for item in page["items"])

    assert ids("categoria=bug&status=classified&needs_review=false") == ["m0"]
    assert ids("categoria=datos&status=classified&needs_review=true") == ["m1"]
    assert ids("categoria=bug&needs_review=true") == []
    assert ids("status=failed&needs_review=false") == ["m2"]


def test_threshold_change_flips_needs_review_over_http(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([make_call()]))  # confianza 0.86
    assert client.post("/solicitudes", json=BODY, headers=HEADERS).json()["needs_review"] is False
    strict, _ = make_client(tmp_path, FakeAdapter([]), CONFIDENCE_THRESHOLD="0.9")
    assert strict.get("/solicitudes/MSG-01", headers=HEADERS).json()["needs_review"] is True
    page = strict.get("/solicitudes?needs_review=true", headers=HEADERS).json()
    assert [item["id"] for item in page["items"]] == ["MSG-01"]


def test_get_detail_keys_404_and_bad_id(client):
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    detail = client.get("/solicitudes/MSG-01", headers=HEADERS).json()
    assert set(detail) == ITEM_KEYS | {"original_classification", "corrections"}
    assert detail["original_classification"]["categoria"] == "bug"
    assert detail["corrections"] == []
    missing = client.get("/solicitudes/NOPE", headers=HEADERS)
    assert (missing.status_code, missing.json()["error"]) == (404, "not_found")
    assert client.get("/solicitudes/" + "a" * 65, headers=HEADERS).status_code == 422


def test_patch_flow_and_errors(client):
    client.post("/solicitudes", json=BODY, headers=HEADERS)
    ok = client.patch(
        "/solicitudes/MSG-01", json={"categoria": "datos", "author": "ana"}, headers=HEADERS
    )
    assert (ok.status_code, ok.json()["categoria"], ok.json()["corrected"]) == (200, "datos", True)
    detail = client.get("/solicitudes/MSG-01", headers=HEADERS).json()
    assert detail["original_classification"]["categoria"] == "bug"
    assert detail["corrections"][0]["new_values"] == {"categoria": "datos"}
    rule = client.patch(
        "/solicitudes/MSG-01", json={"requiere_info": False, "author": "ana"}, headers=HEADERS
    )
    assert rule.status_code == 422 and rule.json()["fields"][0]["loc"] == ["body"]
    missing = client.patch(
        "/solicitudes/NOPE", json={"categoria": "datos", "author": "a"}, headers=HEADERS
    )
    assert missing.status_code == 404
    frozen = client.patch(
        "/solicitudes/MSG-01", json={"confianza": 0.1, "author": "a"}, headers=HEADERS
    )
    assert frozen.status_code == 422


def test_stored_mock_rows_are_marked_even_when_running_a_real_provider(tmp_path):
    from pitz_pulse.providers.mock import MockAdapter

    mock_client, _ = make_client(tmp_path, MockAdapter())
    first = mock_client.post("/solicitudes", json=BODY, headers=HEADERS)
    assert first.headers["X-Pitz-Provider"] == "mock"
    real_client, _ = make_client(tmp_path, FakeAdapter([]))  # same DB, non-mock provider
    detail = real_client.get("/solicitudes/MSG-01", headers=HEADERS)
    assert detail.json()["provider"] == "mock"
    assert detail.headers["X-Pitz-Provider"] == "mock"
    assert "X-Pitz-Provider" not in real_client.get("/health").headers


def test_openapi_carries_enum_values_and_error_bodies(client):
    schema = client.get("/openapi.json").json()
    text = str(schema)
    for value in ("automatizacion", "digital_transformation", "pt", "baja"):
        assert value in text
    post = schema["paths"]["/solicitudes"]["post"]["responses"]
    for status in ("401", "409", "422", "500", "502", "503"):
        assert post[status]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/ErrorBody"
        ), status
    assert "404" in schema["paths"]["/solicitudes/{request_id}"]["patch"]["responses"]
