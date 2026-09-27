"""Gate for Spec 02: FastAPI and its TestClient work on the starlette version locked by Spec 01."""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_fastapi_serves_a_sync_route_through_the_test_client():
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).get("/ping")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
