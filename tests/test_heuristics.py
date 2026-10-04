from pathlib import Path

import pytest

from failtriage.classify.heuristics import signals
from failtriage.grouping import group_failures
from failtriage.models import Attempt, FailureGroup, SignalName, Status, TestResult
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
