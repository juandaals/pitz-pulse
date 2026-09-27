import dataclasses
import io
import logging

import pytest
from fakes import VALID_OUTPUT, FakeAdapter, make_call

from pitz_pulse.classifier import ClassificationCrash, ClassificationError, build_classifier
from pitz_pulse.config import parse_llm_settings
from pitz_pulse.logs import JsonFormatter, configure_logging
from pitz_pulse.providers.base import LLMError
from pitz_pulse.schema import RequestInput

SENTINEL = "SENTINELXYZ"


def classifier(responses, retries=1):
    settings = dataclasses.replace(parse_llm_settings({}), invalid_output_retries=retries)
    adapter = FakeAdapter(responses)
    return build_classifier(settings, adapter), adapter


def request(message="hola", source_area="Comercial MX"):
    return RequestInput(id="MSG-01", message=message, source_area=source_area)


def llm_lines(caplog):
    return [r.fields for r in caplog.records if r.getMessage() == "llm_call"]


def test_happy_path_sets_id_and_version_from_code():
    clf, adapter = classifier([make_call({**VALID_OUTPUT})])
    outcome = clf.classify(request())
    assert (outcome.classification.id, outcome.classification.version_prompt) == ("MSG-01", "v1")
    assert len(adapter.calls) == 1 and outcome.attempts[0].outcome == "ok"


def test_model_cannot_set_version_or_id():
    clf, _ = classifier([make_call({**VALID_OUTPUT, "version_prompt": "v9"}), make_call()])
    outcome = clf.classify(request())
    assert outcome.attempts[0].outcome == "invalid_output"
    assert outcome.classification.version_prompt == "v1"


@pytest.mark.parametrize("retries,expected_calls", [(0, 1), (1, 2), (3, 4)])
def test_retry_budget(retries, expected_calls):
    bad = make_call({**VALID_OUTPUT, "resumen": "palabra " * 30})
    clf, adapter = classifier([bad] * expected_calls, retries=retries)
    with pytest.raises(ClassificationError) as info:
        clf.classify(request())
    assert info.value.kind == "invalid_output"
    assert len(adapter.calls) == expected_calls
    assert [a.outcome for a in info.value.attempts] == ["invalid_output"] * expected_calls


def test_feedback_block_has_errors_without_values():
    bad = make_call({**VALID_OUTPUT, "resumen": (SENTINEL + " ") * 30})
    clf, adapter = classifier([bad, make_call()])
    clf.classify(request())
    second = adapter.calls[1]["user"]
    assert second.count("<feedback>") == 1 and "resumen" in second
    assert SENTINEL not in second


@pytest.mark.parametrize(
    "call",
    [
        make_call(None),
        make_call(stop_reason="max_tokens"),
        make_call(stop_reason="refusal"),
    ],
)
def test_missing_or_truncated_output_is_invalid(call):
    clf, _ = classifier([call, make_call()])
    assert clf.classify(request()).attempts[0].outcome == "invalid_output"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("unavailable", "llm_unavailable"),
        ("rejected", "llm_rejected"),
    ],
)
def test_llm_errors_end_after_one_attempt(kind, expected):
    clf, adapter = classifier([LLMError(kind, "X", 12.0)])
    with pytest.raises(ClassificationError) as info:
        clf.classify(request())
    assert info.value.kind == expected and len(adapter.calls) == 1
    assert info.value.attempts[0].latency_ms == 12.0


def test_invalid_then_unavailable_keeps_both_attempts():
    clf, _ = classifier([make_call(None), LLMError("unavailable", "X")])
    with pytest.raises(ClassificationError) as info:
        clf.classify(request())
    assert [a.outcome for a in info.value.attempts] == ["invalid_output", "unavailable"]


def test_unexpected_exception_is_logged_and_keeps_billed_attempts(caplog):
    clf, _ = classifier([make_call(None, input_tokens=70), KeyError(SENTINEL)])
    with caplog.at_level(logging.INFO), pytest.raises(ClassificationCrash) as info:
        clf.classify(request())
    assert info.value.error_type == "KeyError"
    assert [a.outcome for a in info.value.attempts] == ["invalid_output", "error"]
    assert info.value.attempts[0].input_tokens == 70
    lines = llm_lines(caplog)
    assert [line["outcome"] for line in lines] == ["invalid_output", "error"]
    assert SENTINEL not in str(lines)


def test_unknown_error_kind_is_logged_then_crashes(caplog):
    clf, _ = classifier([LLMError("weird", "X")])
    with caplog.at_level(logging.INFO), pytest.raises(ClassificationCrash):
        clf.classify(request())
    assert [line["outcome"] for line in llm_lines(caplog)] == ["error"]


def test_one_log_line_per_attempt_with_required_fields(caplog):
    clf, _ = classifier([make_call(None), make_call()])
    with caplog.at_level(logging.INFO):
        clf.classify(request(message=f"tel 9999-9999 {SENTINEL}", source_area="a@example.com"))
    lines = llm_lines(caplog)
    assert [line["attempt"] for line in lines] == [1, 2]
    required = {
        "message_id",
        "provider",
        "model",
        "actual_model",
        "prompt_version",
        "attempt",
        "outcome",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "equivalent_api_cost_usd",
        "billing",
        "transport_retries",
        "pii_masked",
    }
    assert all(required <= set(line) for line in lines)
    assert lines[0]["pii_masked"] == {"phone": 1, "email": 1}


def test_no_text_in_any_log_output():
    stream = io.StringIO()
    configure_logging("DEBUG")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logging.getLogger().addHandler(handler)
    bad = make_call({**VALID_OUTPUT, "resumen": (SENTINEL + " ") * 30})
    clf, _ = classifier([bad, make_call()])
    clf.classify(request(message=f"{SENTINEL} hola", source_area=f"{SENTINEL} area"))
    output = stream.getvalue()
    assert output.count('"event": "llm_call"') == 2  # the check is not vacuous
    assert SENTINEL not in output


def test_pii_and_id_never_reach_adapter():
    clf, adapter = classifier([make_call()])
    clf.classify(
        request(
            message="mail b@example.com tel 9999-9999", source_area="Ventas a@example.com 8888-8888"
        )
    )
    user = adapter.calls[0]["user"]
    for raw in ("MSG-01", "b@example.com", "9999-9999", "a@example.com", "8888-8888"):
        assert raw not in user
    assert user.count("[EMAIL]") == 2 and user.count("[PHONE]") == 2


def test_tool_strict_follows_caps():
    clf, adapter = classifier([make_call()])
    clf.classify(request())
    assert adapter.calls[0]["tool"].get("strict") is True  # FakeAdapter caps support strict


def test_attempts_sum_usage_and_cost_across_retries():
    clf, _ = classifier(
        [
            make_call(None, input_tokens=100, cost_usd=0.1),
            make_call(input_tokens=150, cost_usd=0.2),
        ]
    )
    outcome = clf.classify(request())
    assert sum(a.input_tokens for a in outcome.attempts) == 250
    assert sum(a.cost_usd for a in outcome.attempts) == pytest.approx(0.3)


def test_deadline_passed_to_adapter():
    clf, adapter = classifier([make_call()])
    clf.classify(request())
    assert adapter.calls[0]["deadline_s"] == 30 * 4 + 3 * 30 + 10


def test_classify_never_traces(monkeypatch):
    from langsmith import utils

    monkeypatch.setenv("LANGSMITH_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    utils.get_env_var.cache_clear()
    clf, _ = classifier([make_call()])  # build_classifier disables tracing
    assert not utils.tracing_is_enabled()
    clf.classify(request())


def test_graph_runs_with_tracing_disabled_even_if_env_enables_it(monkeypatch):
    from langsmith import utils

    seen = []

    class Spy(FakeAdapter):
        def invoke(self, *args):
            seen.append(utils.tracing_is_enabled())
            return super().invoke(*args)

    settings = parse_llm_settings({})
    clf = build_classifier(settings, Spy([make_call()]))
    # Re-enable after build_classifier forced it off: only the wrapper can keep it off now.
    for name in ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2"):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")  # never the network
    utils.get_env_var.cache_clear()
    assert utils.tracing_is_enabled()
    clf.classify(request())
    utils.get_env_var.cache_clear()
    assert seen == [False]


def test_masking_failure_surfaces_as_crash(monkeypatch):
    from pitz_pulse import classifier as classifier_module

    def broken(*_args):
        raise ValueError(SENTINEL)

    monkeypatch.setattr(classifier_module, "mask_request", broken)
    clf, adapter = classifier([make_call()])
    with pytest.raises(ClassificationCrash) as info:
        clf.classify(request())
    assert info.value.error_type == "ValueError" and info.value.attempts == []
    assert adapter.calls == [] and SENTINEL not in str(info.value)


def test_final_validation_failure_surfaces_as_crash_with_attempts(monkeypatch):
    from pitz_pulse import classifier as classifier_module

    class Broken:
        @staticmethod
        def model_validate(_data):
            raise ValueError(SENTINEL)

    monkeypatch.setattr(classifier_module, "Classification", Broken)
    clf, _ = classifier([make_call()])
    with pytest.raises(ClassificationCrash) as info:
        clf.classify(request())
    assert [a.outcome for a in info.value.attempts] == ["ok"]


def test_rejected_attempt_carries_error_type():
    clf, _ = classifier([LLMError("rejected", "APIStatusError:401")])
    with pytest.raises(ClassificationError) as info:
        clf.classify(request())
    assert info.value.kind == "llm_rejected"
    assert info.value.attempts[-1].error_type == "APIStatusError:401"
