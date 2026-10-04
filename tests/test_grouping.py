from pathlib import Path

from failtriage.grouping import group_failures, signature
from failtriage.models import Attempt, Status, TestResult
from failtriage.parsers.junit import parse_junit

ROOT = Path(__file__).parent.parent
CASES = ROOT / "evals" / "cases"
FIXTURES = ROOT / "tests" / "fixtures" / "junit"


def failure(
    test_id: str,
    message: str | None = "AssertionError: assert 1 == 2",
    stack_trace: str | None = "def test_a():\n>   f()\n\ntests/a.py:3: AssertionError",
    status: Status = Status.FAILED,
) -> TestResult:
    attempt = Attempt(status=status, message=message, stack_trace=stack_trace)
    return TestResult(test_id=test_id, status=status, attempts=[attempt])


def test_forty_failures_with_one_cause_become_one_group() -> None:
    results = parse_junit(CASES / "product-bug-deposit-float" / "junit.xml")

    [group] = group_failures(results)

    assert len(group.results) == 41
    assert group.signature.exception_type == "TypeError"
    assert group.signature.frame == "wallet/accounts.py:deposit"


def test_unrelated_failures_in_one_report_stay_in_separate_groups() -> None:
    groups = group_failures(parse_junit(FIXTURES / "mixed.xml"))

    assert [[r.test_id for r in g.results] for g in groups] == [
        ["tests.test_cart::test_total_with_discount"],
        ["tests/test_api.py::test_fetch_profile"],
        ["tests.test_db::test_migrations_apply"],
    ]


def test_every_failing_test_of_every_lab_case_lands_in_a_group() -> None:
    for junit in sorted(CASES.glob("*/junit.xml")):
        results = parse_junit(junit)
        failing = [r for r in results if r.status not in (Status.PASSED, Status.SKIPPED)]
        groups = group_failures(results)

        assert sum(len(g.results) for g in groups) == len(failing), junit.parent.name


def test_failures_that_differ_only_in_volatile_values_share_a_group() -> None:
    first = failure("a::one", "ConnectionError: refused at 10.0.0.1 port 8080 (id 41)")
    second = failure("a::two", "ConnectionError: refused at 10.0.0.1 port 8081 (id 97)")

    assert len(group_failures([first, second])) == 1


def test_a_different_exception_type_is_a_different_group() -> None:
    first = failure("a::one", "ValueError: bad input")
    second = failure("a::two", "KeyError: bad input")

    assert len(group_failures([first, second])) == 2


def test_a_different_message_is_a_different_group() -> None:
    first = failure("a::one", "AssertionError: assert total == discounted")
    second = failure("a::two", "AssertionError: assert total == shipping")

    assert len(group_failures([first, second])) == 2


def test_the_same_message_from_different_functions_is_a_different_group() -> None:
    first = failure("a::one", stack_trace="def test_a():\n>   f()\n\ntests/a.py:3: AssertionError")
    second = failure("a::two", stack_trace="def test_b():\n>   f()\n\ntests/a.py:9: AssertionError")

    assert len(group_failures([first, second])) == 2


def test_the_line_number_does_not_split_a_group() -> None:
    first = failure("a::one", stack_trace="def test_a():\n>   f()\n\ntests/a.py:3: AssertionError")
    second = failure(
        "a::two", stack_trace="def test_a():\n>   f()\n\ntests/a.py:30: AssertionError"
    )

    assert len(group_failures([first, second])) == 1


def test_the_signature_is_computed_on_redacted_text() -> None:
    first = failure("a::one", "ValueError: no account for jane.doe@example.com")
    second = failure("a::two", "ValueError: no account for john.roe@example.org")

    [group] = group_failures([first, second])

    assert group.signature.message == "no account for <EMAIL>"
    assert "example" not in group.signature.model_dump_json()


def test_a_secret_in_the_message_never_reaches_the_signature() -> None:
    result = failure("a::one", "RuntimeError: login failed, API_TOKEN=hunter2")

    assert "hunter2" not in signature(result).message


def test_the_last_failed_attempt_decides_the_signature() -> None:
    result = TestResult(
        test_id="a::one",
        status=Status.FAILED,
        attempts=[
            Attempt(status=Status.FAILED, message="ValueError: first"),
            Attempt(status=Status.FAILED, message="KeyError: second"),
        ],
    )

    assert signature(result).exception_type == "KeyError"


def test_a_test_that_passed_on_retry_is_grouped_by_the_failed_attempt() -> None:
    result = TestResult(
        test_id="a::one",
        status=Status.PASSED_ON_RETRY,
        attempts=[
            Attempt(status=Status.FAILED, message="TimeoutError: timed out"),
            Attempt(status=Status.PASSED),
        ],
    )

    [group] = group_failures([result])

    assert group.signature.exception_type == "TimeoutError"


def test_passed_and_skipped_tests_are_not_grouped() -> None:
    results = [
        TestResult(test_id="a::ok", status=Status.PASSED, attempts=[Attempt(status=Status.PASSED)]),
        TestResult(
            test_id="a::skip", status=Status.SKIPPED, attempts=[Attempt(status=Status.SKIPPED)]
        ),
    ]

    assert group_failures(results) == []


def test_a_failure_without_any_text_still_gets_a_group() -> None:
    result = failure("a::one", message=None, stack_trace=None)

    [group] = group_failures([result])

    assert group.signature.exception_type == ""
    assert group.signature.frame is None


def test_groups_are_ordered_by_size_then_by_first_appearance() -> None:
    results = [
        failure("a::one", "ValueError: x"),
        failure("a::two", "KeyError: y"),
        failure("a::three", "KeyError: y"),
        failure("a::four", "OSError: z"),
    ]

    groups = group_failures(results)

    assert [g.signature.exception_type for g in groups] == ["KeyError", "ValueError", "OSError"]
    assert [r.test_id for r in groups[0].results] == ["a::two", "a::three"]


def test_grouping_twice_gives_the_same_result() -> None:
    results = parse_junit(FIXTURES / "mixed.xml")

    assert group_failures(results) == group_failures(results)
