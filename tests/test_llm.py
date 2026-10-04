import json
from pathlib import Path

import pytest

from failtriage.classify.llm import InvalidAnswerError, classify_with_llm
from failtriage.classify.payload import GroupPayload, Limits, build_payloads
from failtriage.grouping import group_failures
from failtriage.models import Category, ClassifiedBy, Confidence
from failtriage.parsers.junit import parse_junit
from failtriage.prompts import Prompt, load_prompt

FIXTURES = Path(__file__).parent / "fixtures" / "evals" / "cases"


class StubProvider:
    model = "stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer
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
