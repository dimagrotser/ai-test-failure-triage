from pathlib import Path

import pytest

from failtriage.classify.heuristics import heuristic_verdict, signals
from failtriage.grouping import group_failures
from failtriage.models import (
    Attempt,
    Category,
    FailureGroup,
    Signal,
    SignalName,
    Status,
    TestResult,
)
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


def failure(message: str, stack_trace: str | None = None) -> FailureGroup:
    attempt = Attempt(status=Status.FAILED, message=message, stack_trace=stack_trace)
    result = TestResult(test_id="a::one", status=Status.FAILED, attempts=[attempt])
    return group_failures([result])[0]


def group_with_frame(frame: str) -> FailureGroup:
    group = failure("ValueError: bad")
    return group.model_copy(
        update={"signature": group.signature.model_copy(update={"frame": frame})}
    )


@pytest.mark.parametrize(
    ("case", "name", "quote"),
    [
        (
            "environment-ledger-dns",
            SignalName.NETWORK_ERROR,
            "urllib.error.URLError: <urlopen error [Errno 8] nodename nor servname provided, "
            "or not known>",
        ),
        (
            "environment-ledger-down",
            SignalName.NETWORK_ERROR,
            "urllib.error.URLError: <urlopen error [Errno 61] Connection refused>",
        ),
        ("environment-ledger-timeout", SignalName.TIMEOUT, "TimeoutError: timed out"),
        (
            "environment-missing-ledger-url",
            SignalName.MISSING_ENV_OR_PERMISSION,
            "KeyError: 'WALLET_LEDGER_URL'",
        ),
        (
            "environment-read-only-statements",
            SignalName.MISSING_ENV_OR_PERMISSION,
            "PermissionError: [Errno 13] Permission denied: "
            "'/tmp/lab/junit-env/statements/alice.csv'",
        ),
    ],
)
def test_environment_signals_quote_the_line_that_proves_them(
    case: str, name: SignalName, quote: str
) -> None:
    assert quote_of(lab_group(case), name) == quote


def test_a_lab_case_without_environment_text_has_no_environment_signal() -> None:
    names = {s.name for s in signals(lab_group("product-bug-fee-rounding"))}

    assert names.isdisjoint(
        {SignalName.NETWORK_ERROR, SignalName.TIMEOUT, SignalName.MISSING_ENV_OR_PERMISSION}
    )


def test_a_key_error_on_a_plain_key_is_not_a_missing_variable() -> None:
    group = failure("KeyError: 'EUR'")

    assert quote_of(group, SignalName.MISSING_ENV_OR_PERMISSION) is None


def test_a_word_in_a_library_comment_inside_the_trace_is_not_a_signal() -> None:
    trace = (
        "    except OSError as err: # timeout error\n>   raise ValueError(err)\nE   ValueError: bad"
    )

    assert quote_of(failure("ValueError: bad", trace), SignalName.TIMEOUT) is None


def test_a_signal_is_found_in_the_pytest_exception_line_when_the_message_is_short() -> None:
    trace = ">   call()\nE   requests.exceptions.ConnectionError: Connection refused by host"

    assert quote_of(failure("boom", trace), SignalName.NETWORK_ERROR) == (
        "requests.exceptions.ConnectionError: Connection refused by host"
    )


def test_java_connect_exceptions_are_network_errors() -> None:
    trace = "java.net.ConnectException: Connection refused\n\tat java.base/Socket.connect(S.java:1)"

    group = failure("Connection refused", trace)

    assert quote_of(group, SignalName.NETWORK_ERROR) == "Connection refused"


def test_a_secret_in_the_message_never_reaches_a_quote() -> None:
    group = failure("ConnectionError: refused for jane.doe@example.com API_TOKEN=hunter2")

    quote = quote_of(group, SignalName.NETWORK_ERROR)

    assert quote == "ConnectionError: refused for <EMAIL> API_TOKEN=<SECRET>"


def test_a_frame_in_a_tests_directory_is_test_code() -> None:
    group = lab_group("test-bug-stale-selector")

    assert quote_of(group, SignalName.FRAME_IN_TEST_CODE) == (
        "tests/test_receipt.py:32: LookupError"
    )
    assert quote_of(group, SignalName.FRAME_IN_SOURCE_CODE) is None


def test_a_frame_in_the_application_is_source_code() -> None:
    group = lab_group("product-bug-deposit-float")

    assert quote_of(group, SignalName.FRAME_IN_SOURCE_CODE) == "wallet/accounts.py:16: TypeError"
    assert quote_of(group, SignalName.FRAME_IN_TEST_CODE) is None


@pytest.mark.parametrize(
    "frame",
    [
        "src/cart.spec.ts:total",
        "src/cart.test.js:total",
        "pkg/cart_test.py:test_total",
        "pkg/test_cart.py:test_total",
        "__tests__/cart.js:total",
        "com.acme.CartTest.testTotal",
        "com.acme.CartTests.testTotal",
    ],
)
def test_conventional_test_locations_are_test_code(frame: str) -> None:
    group = group_with_frame(frame)

    assert quote_of(group, SignalName.FRAME_IN_TEST_CODE) is not None
    assert quote_of(group, SignalName.FRAME_IN_SOURCE_CODE) is None


@pytest.mark.parametrize("frame", ["src/cart.ts:total", "com.acme.Cart.total", "latest/cart.py:f"])
def test_other_locations_are_source_code(frame: str) -> None:
    group = group_with_frame(frame)

    assert quote_of(group, SignalName.FRAME_IN_SOURCE_CODE) is not None
    assert quote_of(group, SignalName.FRAME_IN_TEST_CODE) is None


def test_a_group_without_a_frame_has_no_frame_signal() -> None:
    names = {s.name for s in signals(failure("ValueError: bad"))}

    assert names.isdisjoint({SignalName.FRAME_IN_TEST_CODE, SignalName.FRAME_IN_SOURCE_CODE})


@pytest.mark.parametrize(
    ("case", "quote"),
    [
        (
            "product-bug-fee-rounding",
            "AssertionError: assert Decimal('1.48') == Decimal('1.49')",
        ),
        ("flaky-cold-settlement-clock", "assert False"),
    ],
)
def test_an_assertion_error_is_an_assertion_mismatch(case: str, quote: str) -> None:
    assert quote_of(lab_group(case), SignalName.ASSERTION_MISMATCH) == quote


def test_other_exceptions_are_not_an_assertion_mismatch() -> None:
    group = lab_group("product-bug-deposit-float")

    assert quote_of(group, SignalName.ASSERTION_MISMATCH) is None


def verdict_of(*names: SignalName) -> Category | None:
    return heuristic_verdict([Signal(name=n, quote="q") for n in names])


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ([SignalName.PASSED_ON_RETRY], Category.FLAKY),
        ([SignalName.NETWORK_ERROR], Category.ENVIRONMENT),
        ([SignalName.TIMEOUT], Category.ENVIRONMENT),
        ([SignalName.MISSING_ENV_OR_PERMISSION], Category.ENVIRONMENT),
        ([SignalName.FRAME_IN_TEST_CODE], Category.TEST_BUG),
        ([SignalName.FRAME_IN_SOURCE_CODE], Category.PRODUCT_BUG),
    ],
)
def test_each_rule_gives_its_category(names: list[SignalName], expected: Category) -> None:
    assert verdict_of(*names) is expected


@pytest.mark.parametrize(
    "names",
    [
        [],
        [SignalName.ASSERTION_MISMATCH],
        [SignalName.FRAME_IN_TEST_CODE, SignalName.ASSERTION_MISMATCH],
        [SignalName.FRAME_IN_SOURCE_CODE, SignalName.ASSERTION_MISMATCH],
    ],
)
def test_no_clear_signal_means_no_verdict(names: list[SignalName]) -> None:
    assert verdict_of(*names) is None


def test_passed_on_retry_wins_over_every_other_signal() -> None:
    names = [SignalName.NETWORK_ERROR, SignalName.FRAME_IN_SOURCE_CODE, SignalName.PASSED_ON_RETRY]

    assert verdict_of(*names) is Category.FLAKY


def test_an_environment_signal_wins_over_a_frame_signal() -> None:
    assert verdict_of(SignalName.FRAME_IN_TEST_CODE, SignalName.TIMEOUT) is Category.ENVIRONMENT


def test_the_order_of_signals_does_not_change_the_verdict() -> None:
    first = verdict_of(SignalName.TIMEOUT, SignalName.NETWORK_ERROR, SignalName.FRAME_IN_TEST_CODE)
    second = verdict_of(SignalName.FRAME_IN_TEST_CODE, SignalName.NETWORK_ERROR, SignalName.TIMEOUT)

    assert first is second is Category.ENVIRONMENT


def test_a_failure_that_never_passed_is_not_flaky_even_if_it_times_out() -> None:
    group = failure("TimeoutError: timed out")

    assert heuristic_verdict(signals(group)) is Category.ENVIRONMENT


def test_the_same_failure_that_passed_on_retry_is_flaky() -> None:
    result = TestResult(
        test_id="a::one",
        status=Status.PASSED_ON_RETRY,
        attempts=[
            Attempt(status=Status.FAILED, message="TimeoutError: timed out"),
            Attempt(status=Status.PASSED),
        ],
    )

    [group] = group_failures([result])

    assert heuristic_verdict(signals(group)) is Category.FLAKY


def test_a_group_without_text_has_no_signal_and_no_verdict() -> None:
    group = failure("")

    assert signals(group) == []
    assert heuristic_verdict(signals(group)) is None


LAB_VERDICTS = {
    "environment-ledger-dns": Category.ENVIRONMENT,
    "environment-ledger-down": Category.ENVIRONMENT,
    "environment-ledger-timeout": Category.ENVIRONMENT,
    "environment-missing-ledger-url": Category.ENVIRONMENT,
    "environment-read-only-statements": Category.ENVIRONMENT,
    "flaky-cold-settlement-clock": Category.FLAKY,
    "flaky-random-transfer-id": Category.FLAKY,
    "flaky-rates-cache-order": Category.FLAKY,
    "product-bug-deposit-float": Category.PRODUCT_BUG,
    "product-bug-fee-rounding": None,
    "test-bug-expected-value": None,
    "test-bug-fixture-leak": None,
    "test-bug-stale-selector": Category.TEST_BUG,
    "unknown-dormant-new-account": None,
    "unknown-interest-rate-mismatch": None,
    # Known miss: a source-code exception that is really a missing feature, not a bug.
    "unknown-amount-with-comma": Category.PRODUCT_BUG,
}


@pytest.mark.parametrize("case", sorted(LAB_VERDICTS))
def test_verdict_of_the_main_group_of_a_lab_case(case: str) -> None:
    assert heuristic_verdict(signals(lab_group(case))) is LAB_VERDICTS[case]


def test_every_lab_case_is_in_the_verdict_table() -> None:
    cases = {p.parent.name for p in CASES.glob("*/junit.xml")}

    assert cases == set(LAB_VERDICTS)


def test_signals_and_verdict_are_the_same_on_every_call() -> None:
    group = lab_group("environment-ledger-dns")

    assert signals(group) == signals(group)


def test_a_key_error_on_a_lowercase_key_is_not_a_missing_variable() -> None:
    group = failure("KeyError: 'user_id'")

    assert quote_of(group, SignalName.MISSING_ENV_OR_PERMISSION) is None


def test_a_passed_on_retry_result_without_a_passed_attempt_does_not_crash() -> None:
    result = TestResult(
        test_id="a::one",
        status=Status.PASSED_ON_RETRY,
        attempts=[Attempt(status=Status.FAILED, message="ValueError: bad")],
    )

    [group] = group_failures([result])

    assert quote_of(group, SignalName.PASSED_ON_RETRY) == "a::one: failed, then passed on retry"


def test_a_checkout_directory_called_test_does_not_make_source_code_test_code() -> None:
    group = group_with_frame("/home/runner/work/test/app/wallet/accounts.py:deposit")

    assert quote_of(group, SignalName.FRAME_IN_SOURCE_CODE) is not None
    assert quote_of(group, SignalName.FRAME_IN_TEST_CODE) is None


def test_an_absolute_path_inside_a_tests_directory_is_still_test_code() -> None:
    group = group_with_frame("/home/runner/work/app/tests/unit/helpers.py:build")

    assert quote_of(group, SignalName.FRAME_IN_TEST_CODE) is not None
