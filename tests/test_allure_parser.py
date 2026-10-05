from pathlib import Path

from failtriage.models import Status, TestResult
from failtriage.parsers.allure import parse_allure

FIXTURES = Path(__file__).parent / "fixtures" / "allure"


def by_id(name: str) -> dict[str, TestResult]:
    return {r.test_id: r for r in parse_allure(FIXTURES / name)}


def test_only_result_files_become_tests() -> None:
    assert len(parse_allure(FIXTURES / "mixed")) == 5


def test_the_test_class_and_the_name_make_the_test_id() -> None:
    assert "tests.login.LoginTest::shows welcome" in by_id("mixed")


def test_without_a_test_class_the_package_is_used_and_parameters_are_dropped() -> None:
    assert "tests.test_fees::test_fee" in by_id("mixed")


def test_without_labels_the_test_id_comes_from_the_full_name() -> None:
    assert "tests.promo::skips expired code" in by_id("mixed")


def test_allure_statuses_map_to_statuses() -> None:
    results = by_id("mixed")

    assert results["tests.login.LoginTest::shows welcome"].status is Status.PASSED
    assert results["tests.checkout.CheckoutTest::pays by card"].status is Status.FAILED
    assert results["tests.test_fees::test_fee"].status is Status.ERROR
    assert results["tests.promo::skips expired code"].status is Status.SKIPPED
    assert results["tests.odd::odd status"].status is Status.ERROR


def test_a_failed_attempt_has_message_trace_and_duration_in_seconds() -> None:
    [attempt] = by_id("mixed")["tests.checkout.CheckoutTest::pays by card"].attempts

    assert attempt.message == "AssertionError: expected 100 but was 90"
    assert attempt.stack_trace is not None
    assert 'File "tests/checkout.py", line 21' in attempt.stack_trace
    assert attempt.duration == 1.5


def test_a_passed_attempt_has_no_message() -> None:
    [attempt] = by_id("mixed")["tests.login.LoginTest::shows welcome"].attempts

    assert attempt.message is None
    assert attempt.stack_trace is None


def test_result_files_with_one_history_id_are_attempts_of_one_test_ordered_by_start() -> None:
    results = by_id("retries")

    assert len(results) == 3
    flaky = results["tests.pay.PayTest::flaky_pay"]
    assert flaky.status is Status.PASSED_ON_RETRY
    assert [a.status for a in flaky.attempts] == [Status.FAILED, Status.FAILED, Status.PASSED]
    assert [a.message for a in flaky.attempts[:2]] == ["Timeout one", "Timeout two"]


def test_failing_every_attempt_keeps_the_last_status_and_all_attempts() -> None:
    result = by_id("retries")["tests.pay.PayTest::always_broken"]

    assert result.status is Status.ERROR
    assert [a.message for a in result.attempts] == ["boom one", "boom two"]


def test_attachment_paths_of_the_result_and_its_steps_are_kept() -> None:
    [attempt] = by_id("mixed")["tests.checkout.CheckoutTest::pays by card"].attempts

    assert attempt.attachments == [
        str(FIXTURES / "mixed" / "shot-1.png"),
        str(FIXTURES / "mixed" / "missing-log.txt"),
        str(FIXTURES / "mixed" / "step-response.json"),
    ]


def test_attachment_contents_are_never_read() -> None:
    results = parse_allure(FIXTURES / "mixed")

    assert "ATTACHMENT-CONTENT-MARKER" not in repr(results)


def test_steps_keep_name_status_message_and_nesting() -> None:
    [attempt] = by_id("mixed")["tests.checkout.CheckoutTest::pays by card"].attempts

    assert [(s.name, s.status, s.message) for s in attempt.steps] == [
        ("open cart", Status.PASSED, None),
        ("pay", Status.FAILED, "total mismatch"),
    ]
    [nested] = attempt.steps[1].steps
    assert (nested.name, nested.status, nested.message) == (
        "submit form",
        Status.FAILED,
        "422 from /pay",
    )


def test_a_test_without_steps_has_none() -> None:
    [attempt] = by_id("mixed")["tests.login.LoginTest::shows welcome"].attempts

    assert attempt.steps == []
