from pathlib import Path

from failtriage.models import Status
from failtriage.parsers.junit import parse_junit

FIXTURES = Path(__file__).parent / "fixtures" / "junit"


def test_failed_test_has_id_status_message_and_stack_trace() -> None:
    results = parse_junit(FIXTURES / "mixed.xml")

    result = next(r for r in results if r.test_id == "tests.test_cart::test_total_with_discount")
    assert result.status is Status.FAILED
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.message == "assert 90 == 95"
    assert attempt.stack_trace is not None
    assert "tests/test_cart.py:21: AssertionError" in attempt.stack_trace
