from pathlib import Path

import pytest

from failtriage.models import Status, TestResult
from failtriage.parsers import ReportParseError
from failtriage.parsers.playwright import parse_playwright

FIXTURES = Path(__file__).parent / "fixtures" / "playwright"


def by_id(name: str) -> dict[str, TestResult]:
    return {r.test_id: r for r in parse_playwright(FIXTURES / name)}


def test_the_project_and_the_describe_path_are_part_of_the_test_id() -> None:
    ids = by_id("mixed.json")

    assert "login.spec.ts::chromium › shows welcome" in ids
    assert "login.spec.ts::firefox › shows welcome" in ids
    assert "login.spec.ts::chromium › checkout › pays by card" in ids
    assert "login.spec.ts::chromium › checkout › promo › skips expired code" in ids


def test_a_test_without_a_project_name_has_no_project_in_its_id() -> None:
    assert "cart.spec.ts::adds item" in by_id("mixed.json")


def test_failed_passed_and_skipped_tests_get_their_own_status() -> None:
    results = by_id("mixed.json")

    assert results["login.spec.ts::chromium › shows welcome"].status is Status.FAILED
    assert results["login.spec.ts::firefox › shows welcome"].status is Status.PASSED
    skipped = results["login.spec.ts::chromium › checkout › promo › skips expired code"]
    assert skipped.status is Status.SKIPPED


def test_a_failed_attempt_has_a_message_and_stack_trace_without_color_codes() -> None:
    [attempt] = by_id("mixed.json")["login.spec.ts::chromium › shows welcome"].attempts

    assert attempt.message == (
        'Error: expect(received).toHaveText(expected)\n\nExpected: "Welcome"\nReceived: "Sign in"'
    )
    assert attempt.stack_trace is not None
    assert "Expected: " in attempt.stack_trace
    assert "at /work/tests/login.spec.ts:14:38" in attempt.stack_trace
    assert "\x1b" not in attempt.stack_trace


def test_stdout_stderr_and_duration_are_read() -> None:
    [attempt] = by_id("mixed.json")["login.spec.ts::chromium › shows welcome"].attempts

    assert attempt.stdout == "loading page\n"
    assert attempt.stderr == "warn: slow\n"
    assert attempt.duration == 1.2


def test_attachment_paths_are_kept_and_attachments_without_a_path_are_dropped() -> None:
    [attempt] = by_id("mixed.json")["login.spec.ts::chromium › shows welcome"].attempts

    assert attempt.attachments == ["/work/test-results/login-chromium/test-failed-1.png"]


def test_a_passed_attempt_has_no_message_and_no_output() -> None:
    [attempt] = by_id("mixed.json")["login.spec.ts::firefox › shows welcome"].attempts

    assert attempt.message is None
    assert attempt.stdout is None
    assert attempt.attachments == []


def test_a_test_that_is_expected_to_fail_and_does_fail_counts_as_passed() -> None:
    assert by_id("mixed.json")["cart.spec.ts::known bug"].status is Status.PASSED


def test_a_test_that_is_expected_to_fail_but_passes_is_a_failure() -> None:
    result = by_id("mixed.json")["cart.spec.ts::fixed but marked"]

    assert result.status is Status.FAILED
    assert result.attempts[0].message == "Expected to fail, but passed."


def test_retries_become_attempts_and_the_test_passed_on_retry() -> None:
    result = by_id("retries.json")["pay.spec.ts::chromium › flaky pay"]

    assert result.status is Status.PASSED_ON_RETRY
    assert [a.status for a in result.attempts] == [Status.FAILED, Status.FAILED, Status.PASSED]
    assert result.attempts[0].message == "Timeout 5000ms exceeded."


def test_failing_every_attempt_stays_failed_with_all_attempts() -> None:
    result = by_id("retries.json")["pay.spec.ts::chromium › always broken"]

    assert result.status is Status.FAILED
    assert [a.message for a in result.attempts] == ["boom one", "boom two"]


def test_an_interrupted_test_is_an_error() -> None:
    assert by_id("retries.json")["pay.spec.ts::chromium › crashed"].status is Status.ERROR


def test_empty_report_has_no_results() -> None:
    assert parse_playwright(FIXTURES / "empty.json") == []


@pytest.mark.parametrize("name", ["broken.json", "zero.json", "missing.json", "wrong_shape.json"])
def test_unreadable_report_raises(name: str) -> None:
    with pytest.raises(ReportParseError):
        parse_playwright(FIXTURES / name)
