from pathlib import Path

import pytest

from failtriage.models import Status
from failtriage.parsers import ReportParseError
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


def test_failed_then_passed_testcase_becomes_one_passed_on_retry_result() -> None:
    results = parse_junit(FIXTURES / "retries_repeated.xml")

    [result] = [r for r in results if r.test_id == "tests.test_checkout::test_pay_with_card"]
    assert result.status is Status.PASSED_ON_RETRY
    assert [a.status for a in result.attempts] == [Status.FAILED, Status.PASSED]
    assert result.attempts[0].message == "TimeoutError: payment gateway did not respond in 5s"


def test_failed_twice_stays_failed_with_both_attempts() -> None:
    results = parse_junit(FIXTURES / "retries_repeated.xml")

    [result] = [r for r in results if r.test_id == "tests.test_checkout::test_apply_coupon"]
    assert result.status is Status.FAILED
    assert len(result.attempts) == 2


def test_parametrized_variants_are_not_collapsed_as_retries() -> None:
    results = parse_junit(FIXTURES / "retries_repeated.xml")

    converts = [r for r in results if r.test_id == "tests.test_rates::test_convert"]
    assert len(converts) == 3
    assert all(len(r.attempts) == 1 for r in converts)


def test_flaky_failures_become_attempts_before_the_final_pass() -> None:
    results = {r.test_id: r for r in parse_junit(FIXTURES / "retries_surefire.xml")}

    result = results["com.acme.shop.OrderServiceTest::shouldReserveStock"]
    assert result.status is Status.PASSED_ON_RETRY
    assert [a.status for a in result.attempts] == [Status.FAILED, Status.FAILED, Status.PASSED]
    first = result.attempts[0]
    assert first.message == "Expected: 3 but was: 2"
    assert first.stack_trace is not None
    assert "OrderServiceTest.java:58" in first.stack_trace
    assert first.stdout == "reserving 3 units of sku-1042"
    assert first.stderr == "WARN stock cache miss"


def test_rerun_failures_become_attempts_after_the_first_failure() -> None:
    results = {r.test_id: r for r in parse_junit(FIXTURES / "retries_surefire.xml")}

    result = results["com.acme.shop.OrderServiceTest::shouldChargeCard"]
    assert result.status is Status.FAILED
    assert [a.status for a in result.attempts] == [Status.FAILED] * 3
    assert result.attempts[0].duration == 1.87
    assert result.attempts[1].stdout == "retrying charge for order 77"
    assert result.attempts[2].message == "Connection refused"


def test_rerun_errors_keep_the_error_status() -> None:
    [result] = parse_junit(FIXTURES / "retries_errors.xml")

    assert result.status is Status.ERROR
    assert [a.status for a in result.attempts] == [Status.ERROR, Status.ERROR]


def test_a_rerun_that_pytest_rerunfailures_wrote_as_a_plain_testcase_is_a_failed_attempt() -> None:
    results = {r.test_id: r for r in parse_junit(FIXTURES / "pytest_rerunfailures.xml")}

    once = results["tests.test_once::test_fails_once"]
    assert once.status is Status.PASSED_ON_RETRY
    assert [a.status for a in once.attempts] == [Status.FAILED, Status.PASSED]
    assert once.attempts[0].message is None
    always = results["tests.test_once::test_always_fails"]
    assert always.status is Status.FAILED
    assert [a.status for a in always.attempts] == [Status.FAILED, Status.FAILED]
    assert always.attempts[1].message == "AssertionError: never works\nassert False"
    plain = results["tests.test_once::test_plain"]
    assert [a.status for a in plain.attempts] == [Status.PASSED]


def test_a_rerun_written_by_the_flaky_plugin_is_a_failed_attempt() -> None:
    [result] = parse_junit(FIXTURES / "pytest_flaky_plugin.xml")

    assert result.status is Status.PASSED_ON_RETRY
    assert [a.status for a in result.attempts] == [Status.FAILED, Status.PASSED]


def write_report(tmp_path: Path, suite_attributes: str, cases: list[str]) -> Path:
    body = "".join(f'<testcase classname="app.Spec" name="{name}" />' for name in cases)
    path = tmp_path / "report.xml"
    path.write_text(f"<testsuites><testsuite {suite_attributes}>{body}</testsuite></testsuites>")
    return path


def test_repeated_testcases_that_the_suite_counts_are_not_retries(tmp_path: Path) -> None:
    report = write_report(tmp_path, 'tests="2"', ["same title", "same title"])

    [result] = parse_junit(report)

    assert result.status is Status.PASSED
    assert [a.status for a in result.attempts] == [Status.PASSED, Status.PASSED]


def test_a_surplus_that_the_repeats_do_not_explain_is_left_alone(tmp_path: Path) -> None:
    report = write_report(tmp_path, 'tests="1"', ["a", "a", "b", "c"])

    results = {r.test_id: r for r in parse_junit(report)}

    assert {r.status for r in results.values()} == {Status.PASSED}
    assert [a.status for a in results["app.Spec::a"].attempts] == [Status.PASSED, Status.PASSED]


def test_a_suite_without_a_test_count_is_left_alone(tmp_path: Path) -> None:
    report = write_report(tmp_path, 'name="x"', ["a", "a"])

    [result] = parse_junit(report)

    assert result.status is Status.PASSED
