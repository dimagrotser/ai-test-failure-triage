from pathlib import Path

import pytest

from failtriage.models import Status
from failtriage.parsers.junit import ReportParseError, parse_junit

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


def test_error_skipped_and_passed_tests_get_their_own_status() -> None:
    results = {r.test_id: r for r in parse_junit(FIXTURES / "mixed.xml")}

    error = results["tests.test_db::test_migrations_apply"]
    assert error.status is Status.ERROR
    assert error.attempts[0].message is not None
    assert "connection refused" in error.attempts[0].message
    assert results["tests.test_db::test_legacy_import"].status is Status.SKIPPED
    assert results["tests.test_cart::test_empty_cart"].status is Status.PASSED
    assert results["tests.test_cart::test_empty_cart"].attempts[0].message is None


def test_test_id_prefers_file_and_drops_parametrization() -> None:
    ids = [r.test_id for r in parse_junit(FIXTURES / "mixed.xml")]

    assert "tests/test_api.py::test_fetch_profile" in ids
    assert not any("[" in test_id for test_id in ids)


def test_stdout_stderr_and_duration_are_read() -> None:
    results = {r.test_id: r for r in parse_junit(FIXTURES / "mixed.xml")}

    attempt = results["tests.test_cart::test_total_with_discount"].attempts[0]
    assert attempt.stdout == "cart created with 1 item"
    assert attempt.stderr == "WARNING discount rounding is deprecated"
    assert attempt.duration == 0.012


def test_missing_optional_fields_are_none() -> None:
    [result] = parse_junit(FIXTURES / "minimal.xml")

    attempt = result.attempts[0]
    assert attempt.message == "boom"
    assert attempt.stack_trace is None
    assert attempt.stdout is None
    assert attempt.stderr is None
    assert attempt.duration is None


def test_empty_report_has_no_results() -> None:
    assert parse_junit(FIXTURES / "empty.xml") == []


@pytest.mark.parametrize("name", ["broken.xml", "zero.xml", "missing.xml"])
def test_unreadable_report_raises(name: str) -> None:
    with pytest.raises(ReportParseError):
        parse_junit(FIXTURES / name)
