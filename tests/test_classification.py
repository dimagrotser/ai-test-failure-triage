from pathlib import Path

import pytest
from pydantic import ValidationError

from failtriage.classify.heuristics import classify_with_heuristics
from failtriage.grouping import group_failures
from failtriage.history import HistoryEntry, load_history
from failtriage.models import (
    Attempt,
    Category,
    Classification,
    ClassifiedBy,
    Confidence,
    FailureGroup,
    Status,
    TestResult,
)
from failtriage.parsers.junit import parse_junit

CASES = Path(__file__).parent.parent / "evals" / "cases"


def lab_group(case: str) -> FailureGroup:
    return group_failures(parse_junit(CASES / case / "junit.xml"))[0]


def lab_history_of(case: str) -> list[HistoryEntry]:
    return load_history(CASES / case / "history.json")


def test_a_test_that_passed_on_retry_is_flaky_with_high_confidence() -> None:
    result = classify_with_heuristics(lab_group("flaky-random-transfer-id"))

    assert result.category is Category.FLAKY
    assert result.confidence is Confidence.HIGH
    assert result.heuristic_verdict is Category.FLAKY
    assert result.classified_by is ClassifiedBy.HEURISTICS
    assert result.agrees_with_heuristics is None
    assert any("passed on attempt 2" in quote for quote in result.evidence)


def test_a_network_error_is_environment_with_medium_confidence() -> None:
    result = classify_with_heuristics(lab_group("environment-ledger-down"))

    assert result.category is Category.ENVIRONMENT
    assert result.confidence is Confidence.MEDIUM
    assert "Connection refused" in " ".join(result.evidence)


def test_a_failure_in_source_code_is_a_low_confidence_product_bug() -> None:
    result = classify_with_heuristics(lab_group("product-bug-deposit-float"))

    assert result.category is Category.PRODUCT_BUG
    assert result.confidence is Confidence.LOW
    assert result.evidence


def test_a_group_without_signals_is_unknown_with_low_confidence_and_no_evidence() -> None:
    attempt = Attempt(status=Status.FAILED, message="ValueError: bad")
    group = group_failures(
        [TestResult(test_id="a::one", status=Status.FAILED, attempts=[attempt])]
    )[0]

    result = classify_with_heuristics(group)

    assert result.category is Category.UNKNOWN
    assert result.confidence is Confidence.LOW
    assert result.heuristic_verdict is None
    assert result.evidence == []


@pytest.mark.parametrize("category", [Category.PRODUCT_BUG, Category.FLAKY])
def test_a_classification_without_evidence_must_be_unknown(category: Category) -> None:
    with pytest.raises(ValidationError):
        Classification(
            category=category,
            confidence=Confidence.LOW,
            summary="s",
            evidence=[],
            next_step="n",
            classified_by=ClassifiedBy.LLM,
            heuristic_verdict=None,
            agrees_with_heuristics=None,
        )


def test_an_unknown_classification_without_evidence_needs_low_confidence() -> None:
    with pytest.raises(ValidationError):
        Classification(
            category=Category.UNKNOWN,
            confidence=Confidence.HIGH,
            summary="s",
            evidence=[],
            next_step="n",
            classified_by=ClassifiedBy.LLM,
            heuristic_verdict=None,
            agrees_with_heuristics=None,
        )


def test_a_group_where_only_some_tests_passed_on_retry_is_flaky_with_medium_confidence() -> None:
    failed = Attempt(status=Status.FAILED, message="ValueError: bad")
    passed = Attempt(status=Status.PASSED)
    retried = TestResult(
        test_id="a::retried", status=Status.PASSED_ON_RETRY, attempts=[failed, passed]
    )
    always = TestResult(test_id="a::always", status=Status.FAILED, attempts=[failed, failed])
    group = group_failures([retried, always])[0]

    result = classify_with_heuristics(group)

    assert result.category is Category.FLAKY
    assert result.confidence is Confidence.MEDIUM


def llm_classification(
    category: Category, verdict: Category | None, reason: str | None
) -> Classification:
    return Classification(
        category=category,
        confidence=Confidence.MEDIUM,
        summary="s",
        evidence=["quote"],
        next_step="n",
        classified_by=ClassifiedBy.LLM,
        heuristic_verdict=verdict,
        agrees_with_heuristics=None if verdict is None else category is verdict,
        disagreement_reason=reason,
    )


def test_an_llm_classification_that_disagrees_with_the_verdict_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="disagreement_reason"):
        llm_classification(Category.TEST_BUG, Category.PRODUCT_BUG, reason=None)


def test_a_blank_reason_does_not_count() -> None:
    with pytest.raises(ValidationError, match="disagreement_reason"):
        llm_classification(Category.TEST_BUG, Category.PRODUCT_BUG, reason="  ")


def test_an_llm_classification_that_disagrees_keeps_its_reason() -> None:
    result = llm_classification(Category.TEST_BUG, Category.PRODUCT_BUG, reason="stale value")

    assert result.disagreement_reason == "stale value"


def test_an_llm_classification_that_agrees_needs_no_reason() -> None:
    llm_classification(Category.PRODUCT_BUG, Category.PRODUCT_BUG, reason=None)


def test_an_llm_classification_without_a_verdict_needs_no_reason() -> None:
    llm_classification(Category.PRODUCT_BUG, None, reason=None)


def history_of(*statuses: Status, sha: str = "c" * 40) -> list[HistoryEntry]:
    return [
        HistoryEntry(
            test_id="tests.test_balance::test_deposit_increases_the_balance",
            status=status,
            attempts=1,
            sha=sha,
            run_id=n,
        )
        for n, status in enumerate(statuses, start=1)
    ]


def test_a_test_that_passed_and_failed_on_one_sha_in_history_is_flaky_with_medium_confidence() -> (
    None
):
    group = lab_group("product-bug-deposit-float")
    history = history_of(Status.FAILED, Status.PASSED)
    history = [e.model_copy(update={"test_id": group.results[0].test_id}) for e in history]

    result = classify_with_heuristics(group, history=history)

    assert result.category is Category.FLAKY
    assert result.confidence is Confidence.MEDIUM
    assert result.heuristic_verdict is Category.FLAKY
    assert result.summary == "Passed and failed on the same commit in history"
    assert any("passed and failed on" in quote for quote in result.evidence)


def test_a_failure_on_main_alone_does_not_make_a_group_flaky() -> None:
    group = lab_group("product-bug-deposit-float")
    history = [
        e.model_copy(update={"test_id": group.results[0].test_id})
        for e in history_of(Status.FAILED)
    ]

    result = classify_with_heuristics(group, history=history)

    assert result.category is Category.PRODUCT_BUG
    assert any("failed on main" in quote for quote in result.evidence)


def test_passed_on_retry_keeps_high_confidence_when_history_agrees() -> None:
    group = lab_group("flaky-random-transfer-id")
    history = lab_history_of("flaky-random-transfer-id")

    result = classify_with_heuristics(group, history=history)

    assert result.category is Category.FLAKY
    assert result.confidence is Confidence.HIGH
    assert result.summary == "Failed, then passed on retry"
