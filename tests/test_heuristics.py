from pathlib import Path

from failtriage.classify.heuristics import signals
from failtriage.grouping import group_failures
from failtriage.models import FailureGroup, SignalName
from failtriage.parsers.junit import parse_junit

CASES = Path(__file__).parent.parent / "evals" / "cases"


def lab_group(case: str) -> FailureGroup:
    return group_failures(parse_junit(CASES / case / "junit.xml"))[0]


def quote_of(group: FailureGroup, name: SignalName) -> str | None:
    return next((s.quote for s in signals(group) if s.name is name), None)


def test_a_test_that_passed_on_retry_has_the_passed_on_retry_signal() -> None:
    group = lab_group("flaky-random-transfer-id")

    quote = quote_of(group, SignalName.PASSED_ON_RETRY)

    test_id = "tests.test_ids::test_three_transfers_get_different_ids"
    assert quote == f"{test_id}: failed, then passed on attempt 2"
