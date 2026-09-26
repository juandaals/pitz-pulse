import json
import logging
import sys

from pitz_pulse.logs import JsonFormatter, configure_logging, log_event

# Literal list (not the tuple under test).
PINNED = ("anthropic", "httpx", "httpcore", "langchain", "langgraph", "langsmith")


def test_third_party_loggers_pinned_even_at_debug():
    configure_logging("DEBUG")
    for name in PINNED:
        assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
    assert not logging.getLogger("claude_agent_sdk").isEnabledFor(logging.ERROR)
    assert logging.getLogger("pitz_pulse.llm").isEnabledFor(logging.DEBUG)


def test_llm_call_lines_survive_warning_level():
    configure_logging("WARNING")
    assert logging.getLogger("pitz_pulse.llm").isEnabledFor(logging.INFO)


def test_configure_logging_keeps_foreign_handlers(caplog):
    configure_logging("INFO")
    configure_logging("INFO")
    logging.getLogger("pitz_pulse.test").info("still captured")
    assert any(r.getMessage() == "still captured" for r in caplog.records)
    own = [h for h in logging.getLogger().handlers if h.get_name() == "pitz_pulse_json"]
    assert len(own) == 1


def test_json_formatter_fields_cannot_override_reserved_keys():
    record = logging.LogRecord("pitz_pulse.llm", logging.INFO, __file__, 1, "llm_call", None, None)
    record.fields = {"attempt": 1, "event": "forged", "level": "forged"}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "llm_call" and payload["level"] == "INFO"
    assert payload["attempt"] == 1


def test_json_formatter_never_includes_exception_message():
    try:
        raise ValueError("SENTINEL-SECRET-TEXT")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    line = JsonFormatter().format(record)
    assert "SENTINEL-SECRET-TEXT" not in line
    assert json.loads(line)["exc_type"] == "ValueError"


def test_log_event_passes_fields(caplog):
    logger = logging.getLogger("pitz_pulse.test")
    with caplog.at_level(logging.INFO, logger="pitz_pulse.test"):
        log_event(logger, "something", a=1)
    assert caplog.records[-1].fields == {"a": 1}
