"""CLI `python -m pitz_pulse.preflight --provider PROVIDER --models MODEL [MODEL ...]`.

Spec 06 §06b: `compare-models` validates every (provider, model, temperature) combination it is
about to run *before* the first billed call, so a typo'd model name fails fast and names itself
instead of burning a paid call first. Reuses `config.parse_llm_settings` (the same validation a
real classify run would hit) with a dummy credential of the right kind, so it never needs a real
one configured, and never calls a provider or the network either way.
"""

import argparse
import os
import sys
from collections.abc import Mapping

from pitz_pulse.config import ConfigError, parse_llm_settings
from pitz_pulse.models_catalog import CATALOG

_CREDENTIAL_VARS = {
    "anthropic_api": "ANTHROPIC_API_KEY",
    "claude_agent_sdk": "CLAUDE_CODE_OAUTH_TOKEN",
}
_DUMMY_VALUES = {
    "ANTHROPIC_API_KEY": "sk-ant-api-preflight-dummy",
    "CLAUDE_CODE_OAUTH_TOKEN": "preflight-dummy-oauth-token",
}


def check_model(provider: str, model: str, env: Mapping[str, str]) -> None:
    """Raise `ConfigError` naming `model` when (provider, model, temperature) would not run.

    Temperature is set to "none" whenever the catalog says the model does not support it (every
    `claude_agent_sdk` model, plus `claude-sonnet-5` over `anthropic_api`) — read from the catalog
    rather than hardcoded, so it stays correct if the catalog changes. An unknown (provider,
    model) is left as "0": `parse_llm_settings` itself raises the (better-worded) "unknown model"
    error for that case.
    """
    candidate = dict(env)
    for name in _CREDENTIAL_VARS.values():
        candidate.pop(name, None)
    needed = _CREDENTIAL_VARS.get(provider)
    if needed:
        candidate[needed] = _DUMMY_VALUES[needed]
    caps = CATALOG.get((provider, model))
    candidate["LLM_PROVIDER"] = provider
    candidate["LLM_MODEL"] = model
    candidate["LLM_TEMPERATURE"] = "none" if caps and not caps.supports_temperature else "0"
    try:
        parse_llm_settings(candidate)
    except ConfigError as exc:
        raise ConfigError(f"{model}: {exc}") from exc


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m pitz_pulse.preflight")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--models", required=True, nargs="+")
    return parser.parse_args(argv)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    for model in args.models:
        try:
            check_model(args.provider, model, env)
        except ConfigError as exc:
            return _fail(str(exc))

    print(f"preflight ok: provider={args.provider} models={' '.join(args.models)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
