"""HTTP-only settings. Composes LLMSettings (D21); never serialized (secrets, catalog checks)."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pitz_pulse.config import ConfigError, LLMSettings, parse_llm_settings

QUEUE_WAIT_S = 30.0  # longest wait for a model slot before 503 busy (Spec 02 §2)
MAX_PENDING_STALE_S = 604800  # 7 days; keeps timestamp arithmetic far from overflow


@dataclass(frozen=True)
class ApiSettings:
    llm: LLMSettings
    api_key: str = field(repr=False)
    db_path: Path
    pending_stale_s: int


def stale_floor_s(llm: LLMSettings, queue_wait_s: float = QUEUE_WAIT_S) -> int:
    """Longest a live request can plausibly stay pending (budget per attempt, D28)."""
    return math.ceil((1 + llm.invalid_output_retries) * llm.deadline_s + queue_wait_s + 60)


def _text(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def parse_api_settings(env: Mapping[str, str]) -> ApiSettings:
    llm = parse_llm_settings(env)
    api_key = _text(env, "API_KEY")
    if not api_key:
        raise ConfigError("API_KEY is required for the HTTP API")
    if not (api_key.isascii() and api_key.isprintable()):
        raise ConfigError("API_KEY must be printable ASCII")
    raw_path = _text(env, "DB_PATH")
    db_path = Path(raw_path) if raw_path else Path("data") / "pitz_pulse.db"
    if not db_path.is_absolute():
        db_path = llm.app_root / db_path  # never the current working directory
    floor = stale_floor_s(llm)
    raw_stale = _text(env, "PENDING_STALE_SECONDS")
    if not raw_stale:
        return ApiSettings(llm, api_key, db_path, floor)
    try:
        stale = int(raw_stale)
    except ValueError:
        raise ConfigError("PENDING_STALE_SECONDS must be an integer") from None
    if stale < floor:
        raise ConfigError(
            f"PENDING_STALE_SECONDS={stale} is below the minimum {floor} derived from "
            "LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES and INVALID_OUTPUT_RETRIES; raise it or unset it"
        )
    if stale > MAX_PENDING_STALE_S:
        raise ConfigError(
            f"PENDING_STALE_SECONDS={stale} is above the maximum {MAX_PENDING_STALE_S} (7 days)"
        )
    return ApiSettings(llm, api_key, db_path, stale)
