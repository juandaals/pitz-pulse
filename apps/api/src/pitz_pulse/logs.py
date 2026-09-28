"""Structured JSON logging. Never logs message text, model output, exception messages or secrets."""

import json
import logging

THIRD_PARTY_LOGGERS = (
    "anthropic",
    "httpx",
    "httpcore",
    "httpx2",  # anthropic >= 1.8 transport
    "httpcore2",
    "langchain",
    "langchain_core",
    "langchain_anthropic",
    "langgraph",
    "langsmith",
    "mcp",
)
OWN_EVENT_LOGGERS = ("pitz_pulse.llm", "pitz_pulse.service", "pitz_pulse.slack")
_HANDLER_NAME = "pitz_pulse_json"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = dict(getattr(record, "fields", {}))
        payload.update(
            ts=self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            level=record.levelname,
            logger=record.name,
            event=record.getMessage(),
        )
        if record.exc_info and record.exc_info[0] is not None:
            # Class name only: exception messages may carry request or model text.
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def pin_third_party_loggers() -> None:
    """Their DEBUG/INFO output includes request bodies; call again after importing an SDK."""
    for name in THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # Its ERROR lines can embed raw CLI output.
    logging.getLogger("claude_agent_sdk").setLevel(logging.CRITICAL)
    # One llm_call line per attempt and one outcome line per request are required (R2.6),
    # whatever LOG_LEVEL is.
    for name in OWN_EVENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if h.get_name() != _HANDLER_NAME]
    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    pin_third_party_loggers()
    if root.isEnabledFor(logging.DEBUG):
        for name in OWN_EVENT_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    logger.info(event, extra={"fields": fields})
