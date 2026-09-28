from fastapi.testclient import TestClient

from pitz_pulse.api import create_app
from pitz_pulse.settings_api import parse_api_settings

KEY = "test-key"
HEADERS = {"X-API-Key": KEY}


def make_client(tmp_path, adapter, *, slack_notifier=None, **env):
    settings = parse_api_settings(
        {"LLM_PROVIDER": "mock", "API_KEY": KEY, "DB_PATH": str(tmp_path / "api.db"), **env}
    )
    app = create_app(settings, adapter=adapter, slack_notifier=slack_notifier)
    return TestClient(app, raise_server_exceptions=False), app
