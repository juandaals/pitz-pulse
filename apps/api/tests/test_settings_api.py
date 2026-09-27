import pytest

from pitz_pulse.config import ConfigError
from pitz_pulse.settings_api import parse_api_settings, stale_floor_s

BASE = {"LLM_PROVIDER": "mock", "API_KEY": "test-key"}


def test_api_key_is_required():
    with pytest.raises(ConfigError, match="API_KEY"):
        parse_api_settings({"LLM_PROVIDER": "mock"})


@pytest.mark.parametrize("key", ["clé-secreta", "tab\tkey"])
def test_api_key_must_be_printable_ascii(key):
    with pytest.raises(ConfigError, match="API_KEY must be printable ASCII"):
        parse_api_settings({**BASE, "API_KEY": key})


def test_stale_window_defaults_to_the_derived_minimum():
    settings = parse_api_settings(BASE)
    assert settings.pending_stale_s == stale_floor_s(settings.llm) == 530


def test_raising_the_timeout_raises_the_default_instead_of_failing():
    settings = parse_api_settings({**BASE, "LLM_TIMEOUT_SECONDS": "60"})
    assert settings.pending_stale_s == 770  # 2 × 340 + 30 + 60


def test_explicit_stale_window_below_the_minimum_names_both_variables():
    with pytest.raises(ConfigError) as info:
        parse_api_settings({**BASE, "PENDING_STALE_SECONDS": "100"})
    message = str(info.value)
    assert "PENDING_STALE_SECONDS" in message
    assert "LLM_TIMEOUT_SECONDS" in message
    assert "530" in message


def test_explicit_stale_window_must_be_an_integer():
    with pytest.raises(ConfigError, match="PENDING_STALE_SECONDS"):
        parse_api_settings({**BASE, "PENDING_STALE_SECONDS": "soon"})


def test_db_path_defaults_under_app_root_and_relative_paths_are_rooted(tmp_path):
    settings = parse_api_settings(BASE)
    assert settings.db_path == settings.llm.app_root / "data" / "pitz_pulse.db"
    settings = parse_api_settings({**BASE, "DB_PATH": "var/x.db"})
    assert settings.db_path == settings.llm.app_root / "var" / "x.db"
    settings = parse_api_settings({**BASE, "DB_PATH": str(tmp_path / "abs.db")})
    assert settings.db_path == tmp_path / "abs.db"


def test_repr_never_shows_keys():
    settings = parse_api_settings({**BASE, "API_KEY": "sentinel-api-key"})
    assert "sentinel-api-key" not in repr(settings)


def test_explicit_real_provider_without_its_key_fails_fast_naming_the_variable():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        parse_api_settings({"LLM_PROVIDER": "anthropic_api", "API_KEY": "k"})
