import json
import logging

import pytest
import uvicorn
from fakes import FakeAdapter
from fastapi.testclient import TestClient

from pitz_pulse.api import create_app
from pitz_pulse.config import ConfigError
from pitz_pulse.settings_api import parse_api_settings


def _env(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "factory.db"))
    for name, value in extra.items():
        monkeypatch.setenv(name, value)


def test_uvicorn_can_load_the_zero_argument_factory(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    config = uvicorn.Config("pitz_pulse.api:create_app", factory=True)
    config.load()
    assert config.loaded_app is not None
    assert (tmp_path / "factory.db").exists()  # migrations ran


def test_missing_api_key_fails_startup_naming_it(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("API_KEY")
    with pytest.raises(ConfigError, match="API_KEY"):
        create_app()


def test_default_adapter_in_mock_env_is_the_mock_and_marks_responses(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    assert client.get("/health").json()["provider"] == "mock"
    assert client.get("/health").headers["X-Pitz-Provider"] == "mock"


def test_one_post_logs_one_llm_call_and_one_outcome_as_json(monkeypatch, tmp_path, capsys):
    _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    capsys.readouterr()
    client.post(
        "/solicitudes",
        json={"id": "L1", "message": "SENTINEL-LOG-TEXT no puedo acceder"},
        headers={"X-API-Key": "test-key"},
    )
    lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")
    ]
    events = [line["event"] for line in lines]
    assert events.count("llm_call") == 1
    assert events.count("request_outcome") == 1
    assert "SENTINEL-LOG-TEXT" not in json.dumps(lines)


class MockNamedAdapter(FakeAdapter):
    provider = "mock"


def test_unhandled_errors_are_a_marked_500_and_never_log_exception_text(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    settings = parse_api_settings(
        {"LLM_PROVIDER": "mock", "API_KEY": "test-key", "DB_PATH": str(tmp_path / "e.db")}
    )
    adapter = MockNamedAdapter([ValueError("SENTINEL-EXC-TEXT")])
    client = TestClient(create_app(settings, adapter=adapter))  # re-raises unhandled errors
    response = client.post(
        "/solicitudes", json={"id": "E1", "message": "hola"}, headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error", "detail": "internal error"}
    assert response.headers["X-Pitz-Provider"] == "mock"
    captured = " ".join(
        [r.getMessage() for r in caplog.records]
        + [str(getattr(r, "fields", "")) for r in caplog.records]
        + [str(r.exc_info) for r in caplog.records if r.exc_info]
    )
    assert "unhandled_error" in captured
    assert "SENTINEL-EXC-TEXT" not in captured
