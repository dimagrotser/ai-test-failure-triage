from pathlib import Path

import pytest

from failtriage.grouping.extract import exception_type, first_message_line, top_frame
from failtriage.models import Attempt, Status
from failtriage.parsers.junit import parse_junit

ROOT = Path(__file__).parent.parent
CASES = ROOT / "evals" / "cases"
FIXTURES = ROOT / "tests" / "fixtures" / "junit"


def failed_attempt(junit: Path, test_name: str | None = None) -> Attempt:
    [result] = [
        r
        for r in parse_junit(junit)
        if r.status not in (Status.PASSED, Status.SKIPPED)
        and (test_name is None or test_name in r.test_id)
    ][:1]
    return [a for a in result.attempts if a.status in (Status.FAILED, Status.ERROR)][-1]


def case_attempt(case: str) -> Attempt:
    return failed_attempt(CASES / case / "junit.xml")


def attempt(message: str | None = None, stack_trace: str | None = None) -> Attempt:
    return Attempt(status=Status.FAILED, message=message, stack_trace=stack_trace)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("AssertionError: assert 1 == 2", "AssertionError"),
        ("urllib.error.URLError: <urlopen error>", "urllib.error.URLError"),
        ("java.lang.AssertionError: expected:<1> but was:<2>", "java.lang.AssertionError"),
        ("Failed: DID NOT RAISE InsufficientFunds", "Failed"),
        ("Error: expect(locator).toBeVisible()", "Error"),
        ("TimeoutError: timed out", "TimeoutError"),
    ],
)
def test_exception_type_comes_from_the_message_prefix(message: str, expected: str) -> None:
    assert exception_type(attempt(message)) == expected


def test_exception_type_falls_back_to_the_last_exception_line_of_the_trace() -> None:
    migration_error = failed_attempt(FIXTURES / "mixed.xml", "test_migrations_apply")

    assert exception_type(migration_error) == "sqlalchemy.exc.OperationalError"


def test_exception_type_falls_back_to_the_pytest_location_line() -> None:
    assert exception_type(case_attempt("flaky-cold-settlement-clock")) == "AssertionError"
    assert exception_type(attempt("assert 90 == 95", "tests/a.py:21: AssertionError")) == (
        "AssertionError"
    )


def test_exception_type_is_empty_when_nothing_names_one() -> None:
    assert exception_type(attempt("something broke", "no location here")) == ""
    assert exception_type(attempt(None, None)) == ""


def test_first_message_line_drops_the_type_prefix_and_later_lines() -> None:
    message = "AssertionError: assert 1 == 2\n  - 2\n  + 1"

    assert first_message_line(attempt(message)) == "assert 1 == 2"


def test_first_message_line_keeps_a_message_without_a_type() -> None:
    assert first_message_line(attempt("assert 90 == 95")) == "assert 90 == 95"


def test_first_message_line_falls_back_to_the_last_exception_line() -> None:
    trace = "def test():\n>   boom()\nE   RuntimeError: it broke\n\ntests/a.py:3: RuntimeError"

    assert first_message_line(attempt(None, trace)) == "it broke"


def test_first_message_line_is_empty_without_any_text() -> None:
    assert first_message_line(attempt(None, None)) == ""


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("environment-ledger-down", "wallet/ledger.py:record_transfer"),
        ("environment-ledger-timeout", "wallet/ledger.py:record_transfer"),
        ("environment-missing-ledger-url", "wallet/ledger.py:record_transfer"),
        ("environment-read-only-statements", "wallet/statements.py:export_statement"),
        ("unknown-amount-with-comma", "wallet/amounts.py:parse_amount"),
        ("flaky-rates-cache-order", "wallet/rates.py:convert"),
        ("product-bug-deposit-float", "wallet/accounts.py:deposit"),
        ("test-bug-stale-selector", "tests/test_receipt.py:text_by_testid"),
        (
            "flaky-cold-settlement-clock",
            "tests/test_settlement.py:test_settlement_is_confirmed_before_the_deadline",
        ),
    ],
)
def test_top_frame_of_a_pytest_trace_is_the_innermost_project_frame(
    case: str, expected: str
) -> None:
    assert top_frame(case_attempt(case)) == expected


def test_top_frame_skips_stdlib_site_packages_and_pytest_internals() -> None:
    trace = (
        "def test_x():\n"
        ">   run()\n\n"
        "tests/test_x.py:4: \n"
        "_ _ _ _\n\n"
        "def run():\n"
        ">   client.get()\n\n"
        "app/client.py:9: in run\n"
        "    client.get()\n"
        "/venv/lib/python3.12/site-packages/requests/api.py:73: in get\n"
        "/stdlib/socket.py:850: in connect\n"
        "/venv/lib/python3.12/site-packages/_pytest/python.py:1: in x\n"
        "<frozen os>:714: KeyError"
    )

    assert top_frame(attempt("KeyError: 'x'", trace)) == "app/client.py:run"


def test_top_frame_of_a_python_traceback() -> None:
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/home/runner/work/app/app/tests/test_cart.py", line 12, in test_total\n'
        "    cart.total()\n"
        '  File "/home/runner/work/app/app/shop/cart.py", line 30, in total\n'
        "    return 1 / 0\n"
        '  File "/opt/hostedtoolcache/Python/3.12.1/x64/lib/python3.12/decimal.py", line 5, in f\n'
        "ZeroDivisionError: division by zero"
    )

    assert top_frame(attempt("ZeroDivisionError: division by zero", trace)) == "shop/cart.py:total"


def test_top_frame_of_a_junit_java_trace_skips_junit_and_the_jdk() -> None:
    trace = (
        "java.lang.AssertionError: expected:<1> but was:<2>\n"
        "\tat org.junit.Assert.fail(Assert.java:89)\n"
        "\tat org.junit.Assert.assertEquals(Assert.java:647)\n"
        "\tat com.acme.shop.CartTest.total(CartTest.java:21)\n"
        "\tat java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)\n"
        "\tat org.apache.maven.surefire.junit4.JUnit4Provider.execute(JUnit4Provider.java:365)\n"
    )

    assert top_frame(attempt("java.lang.AssertionError", trace)) == "com.acme.shop.CartTest.total"


def test_top_frame_of_a_playwright_trace_skips_the_runner_and_node() -> None:
    trace = (
        "Error: expect(locator).toBeVisible()\n"
        "    at /work/node_modules/playwright/lib/matchers/expect.js:40:11\n"
        "    at Object.<anonymous> (/work/e2e/login.spec.ts:12:5)\n"
        "    at async /work/node_modules/@playwright/test/lib/worker.js:9:3\n"
        "    at node:internal/process/task_queues:95:5\n"
    )

    assert top_frame(attempt("Error: expect(locator).toBeVisible()", trace)) == (
        "e2e/login.spec.ts:Object.<anonymous>"
    )


def test_top_frame_of_a_playwright_frame_without_a_function_name() -> None:
    trace = "Error: boom\n    at /work/e2e/login.spec.ts:12:5\n"

    assert top_frame(attempt("Error: boom", trace)) == "e2e/login.spec.ts:"


def test_top_frame_is_none_without_a_project_frame() -> None:
    assert top_frame(attempt("boom", None)) is None
    assert top_frame(attempt("boom", "just text\nnothing to see")) is None
    assert top_frame(attempt("boom", "/stdlib/socket.py:850: in connect")) is None
