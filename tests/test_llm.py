import json
from pathlib import Path

import pytest

from failtriage.classify.llm import InvalidAnswerError, UnredactedPayloadError, classify_with_llm
from failtriage.classify.payload import GroupPayload, Limits, build_payloads
from failtriage.classify.provider import MissingRecordingError, RecordedProvider, Usage
from failtriage.grouping import group_failures
from failtriage.models import Category, ClassifiedBy, Confidence
from failtriage.parsers.junit import parse_junit
from failtriage.prompts import Prompt, load_prompt

FIXTURES = Path(__file__).parent / "fixtures" / "evals" / "cases"
SECRETS = Path(__file__).parent / "fixtures" / "junit" / "secrets.xml"


class StubProvider:
    model = "stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.usage = Usage()
        self.payloads: list[str] = []

    def complete(self, prompt: Prompt, payload: str) -> str:
        self.payloads.append(payload)
        return self.answer


def payload_for(case: str) -> GroupPayload:
    groups = group_failures(parse_junit(FIXTURES / case / "junit.xml"))
    return build_payloads(groups, Limits()).sent[0]


def answer(**fields: object) -> str:
    base: dict[str, object] = {
        "category": "environment",
        "confidence": "medium",
        "summary": "The ledger service refused the connection.",
        "evidence": ["ConnectionRefusedError: [Errno 111] Connection refused"],
        "next_step": "Check that the ledger service is up",
        "disagreement_reason": None,
    }
    return json.dumps(base | fields)


def test_an_agreeing_answer_becomes_an_llm_classification() -> None:
    payload = payload_for("environment-refused")
    assert payload.heuristic_verdict is Category.ENVIRONMENT

    result = classify_with_llm(payload, StubProvider(answer()), load_prompt())

    assert result.category is Category.ENVIRONMENT
    assert result.confidence is Confidence.MEDIUM
    assert result.classified_by is ClassifiedBy.LLM
    assert result.heuristic_verdict is Category.ENVIRONMENT
    assert result.agrees_with_heuristics is True
    assert result.disagreement_reason is None
    assert result.evidence == ["ConnectionRefusedError: [Errno 111] Connection refused"]


def test_the_provider_gets_the_payload_as_json() -> None:
    payload = payload_for("environment-refused")
    provider = StubProvider(answer())

    classify_with_llm(payload, provider, load_prompt())

    assert GroupPayload.model_validate_json(provider.payloads[0]) == payload


def test_a_disagreement_with_a_reason_is_kept() -> None:
    reply = answer(
        category="test_bug", disagreement_reason="The test pins a port that changed in the diff"
    )

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.TEST_BUG
    assert result.agrees_with_heuristics is False
    assert result.disagreement_reason == "The test pins a port that changed in the diff"


def test_a_disagreement_without_a_reason_is_an_invalid_answer() -> None:
    reply = answer(category="test_bug")

    with pytest.raises(InvalidAnswerError):
        classify_with_llm(payload_for("environment-refused"), StubProvider(reply), load_prompt())


def test_a_group_without_a_heuristic_verdict_has_nothing_to_agree_with() -> None:
    payload = payload_for("environment-refused").model_copy(update={"heuristic_verdict": None})

    result = classify_with_llm(payload, StubProvider(answer()), load_prompt())

    assert result.heuristic_verdict is None
    assert result.agrees_with_heuristics is None


@pytest.mark.parametrize(
    "reply",
    ["not json", "{}", answer(category="network"), answer(evidence="a string")],
)
def test_an_answer_that_does_not_fit_the_schema_is_invalid(reply: str) -> None:
    with pytest.raises(InvalidAnswerError):
        classify_with_llm(payload_for("environment-refused"), StubProvider(reply), load_prompt())


def test_a_quote_that_is_not_in_the_payload_is_dropped() -> None:
    reply = answer(
        evidence=[
            "ConnectionRefusedError: [Errno 111] Connection refused",
            "Redis cluster failover at 03:14",
        ]
    )

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.ENVIRONMENT
    assert result.evidence == ["ConnectionRefusedError: [Errno 111] Connection refused"]


def test_a_quote_may_come_from_any_part_of_the_payload() -> None:
    payload = payload_for("environment-refused")
    quotes = [payload.signals[0].quote, payload.tests[0], "wallet/ledger.py:12"]

    result = classify_with_llm(payload, StubProvider(answer(evidence=quotes)), load_prompt())

    assert result.evidence == quotes


def test_a_quote_with_a_newline_is_checked_against_the_raw_text() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"stack_trace": "first line\nsecond line"}
    )

    result = classify_with_llm(
        payload, StubProvider(answer(evidence=["first line\nsecond line"])), load_prompt()
    )

    assert result.evidence == ["first line\nsecond line"]


def test_a_group_left_without_evidence_becomes_unknown_with_low_confidence() -> None:
    reply = answer(category="environment", confidence="high", evidence=["made up"])

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.confidence is Confidence.LOW
    assert result.evidence == []
    assert "not found" in result.summary


def test_dropping_all_evidence_explains_the_change_when_the_verdict_was_something_else() -> None:
    reply = answer(category="test_bug", evidence=["made up"], disagreement_reason="because")

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.agrees_with_heuristics is False
    assert result.disagreement_reason


def test_an_empty_quote_is_not_evidence() -> None:
    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(answer(evidence=[""])), load_prompt()
    )

    assert result.category is Category.UNKNOWN


def test_an_unknown_answer_without_evidence_stays_as_it_is() -> None:
    reply = answer(
        category="unknown", confidence="low", evidence=[], disagreement_reason="no proof"
    )

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.summary == "The ledger service refused the connection."


PLANTED = [
    "hunter2",
    "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "jane.doe@example.com",
    "4111 1111 1111 1111",
    "abc.def.ghi",
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
]


def test_planted_secrets_never_reach_the_provider() -> None:
    groups = group_failures(parse_junit(SECRETS))
    [payload] = build_payloads(groups, Limits()).sent
    reply = answer(
        category="unknown", confidence="low", evidence=[], disagreement_reason="no proof"
    )
    provider = StubProvider(reply)

    classify_with_llm(payload, provider, load_prompt())

    [sent] = provider.payloads
    assert [secret for secret in PLANTED if secret in sent] == []


def test_the_final_check_aborts_the_call_on_an_unredacted_payload() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"stderr": "GITHUB_TOKEN=ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"}
    )
    provider = StubProvider(answer())

    with pytest.raises(UnredactedPayloadError) as error:
        classify_with_llm(payload, provider, load_prompt())

    assert provider.payloads == []
    assert "ghp_" not in str(error.value)


def test_an_already_redacted_payload_passes_the_final_check() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"stderr": "GITHUB_TOKEN=<SECRET> for <EMAIL>"}
    )
    provider = StubProvider(answer())

    classify_with_llm(payload, provider, load_prompt())

    assert len(provider.payloads) == 1


RECORDINGS = Path(__file__).parent / "fixtures" / "llm"


def test_a_recorded_answer_is_replayed_into_a_classification() -> None:
    provider = RecordedProvider(RECORDINGS, "claude-sonnet-5-5")

    result = classify_with_llm(payload_for("environment-refused"), provider, load_prompt())

    assert result.category is Category.ENVIRONMENT
    assert result.agrees_with_heuristics is True


def test_a_recorded_answer_for_a_group_without_a_verdict() -> None:
    provider = RecordedProvider(RECORDINGS, "claude-sonnet-5-5")

    result = classify_with_llm(payload_for("product-bug-assert"), provider, load_prompt())

    assert result.category is Category.PRODUCT_BUG
    assert result.agrees_with_heuristics is None
    assert result.evidence == ["AssertionError: assert 1.49 == 1.5"]


def test_a_payload_without_a_recording_fails_instead_of_calling_out() -> None:
    provider = RecordedProvider(RECORDINGS, "claude-sonnet-5-5")
    payload = payload_for("flaky-retry")

    with pytest.raises(MissingRecordingError):
        classify_with_llm(payload, provider, load_prompt())


def test_an_unknown_answer_without_a_reason_is_kept_when_there_is_nothing_to_quote() -> None:
    reply = answer(category="unknown", confidence="low", evidence=[])

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.agrees_with_heuristics is False
    assert result.disagreement_reason


def test_a_quote_copied_from_the_json_text_is_matched_and_stored_unescaped() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"message": 'assert x == {"a": 1}\\n'}
    )
    quote = 'assert x == {\\"a\\": 1}\\\\n'

    result = classify_with_llm(payload, StubProvider(answer(evidence=[quote])), load_prompt())

    assert result.evidence == ['assert x == {"a": 1}\\n']


def test_a_private_key_in_the_payload_aborts_the_call() -> None:
    key = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----"
    payload = payload_for("environment-refused").model_copy(update={"stderr": key})
    provider = StubProvider(answer())

    with pytest.raises(UnredactedPayloadError):
        classify_with_llm(payload, provider, load_prompt())

    assert provider.payloads == []
