from failtriage.classify.payload import (
    GROUP_CAP_NOTE,
    Limits,
    build_payloads,
    select_hunks,
    truncate_lines,
    truncate_message,
)
from failtriage.models import Attempt, FailureGroup, Signature, Status, TestResult


def numbered(count: int) -> str:
    return "\n".join(f"line {n}" for n in range(1, count + 1))


def test_long_text_keeps_head_and_tail_and_counts_the_omitted_lines() -> None:
    result = truncate_lines(numbered(100), head=15, tail=25).splitlines()

    assert result[:15] == numbered(15).splitlines()
    assert result[15] == "... 60 lines omitted ..."
    assert result[16:] == [f"line {n}" for n in range(76, 101)]


def test_text_shorter_than_the_limits_is_unchanged() -> None:
    assert truncate_lines(numbered(10), head=15, tail=25) == numbered(10)


def test_text_exactly_at_the_limits_is_unchanged() -> None:
    assert truncate_lines(numbered(40), head=15, tail=25) == numbered(40)


def test_one_line_over_the_limits_omits_exactly_one_line() -> None:
    result = truncate_lines(numbered(41), head=15, tail=25)

    assert "... 1 lines omitted ..." in result.splitlines()
    assert "line 16" not in result.splitlines()


def test_empty_text_is_unchanged() -> None:
    assert truncate_lines("", head=15, tail=25) == ""


def test_message_over_the_limit_is_cut_and_counts_the_omitted_characters() -> None:
    result = truncate_message("a" * 1005, limit=1000)

    assert result == "a" * 1000 + "... 5 characters omitted"


def test_message_exactly_at_the_limit_is_unchanged() -> None:
    assert truncate_message("a" * 1000, limit=1000) == "a" * 1000


def test_empty_message_is_unchanged() -> None:
    assert truncate_message("", limit=1000) == ""


DIFF = """\
diff --git a/src/wallet/ledger.py b/src/wallet/ledger.py
index 111..222 100644
--- a/src/wallet/ledger.py
+++ b/src/wallet/ledger.py
@@ -1,2 +1,2 @@
-balance = 0
+balance = 0.0
diff --git a/README.md b/README.md
index 333..444 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
TRACE = '  File "/work/src/wallet/ledger.py", line 9, in deposit'


def test_diff_keeps_only_files_named_in_the_trace() -> None:
    result = select_hunks(DIFF, TRACE, limit=150)

    assert "src/wallet/ledger.py" in result
    assert "+balance = 0.0" in result
    assert "README.md" not in result


def test_diff_without_a_relevant_file_is_empty() -> None:
    assert select_hunks(DIFF, "nothing here", limit=150) == ""


def test_empty_diff_is_empty() -> None:
    assert select_hunks("", TRACE, limit=150) == ""


def test_relevant_diff_over_the_line_limit_is_cut_with_a_marker() -> None:
    result = select_hunks(DIFF, TRACE, limit=3).splitlines()

    assert result[:3] == DIFF.splitlines()[:3]
    assert result[3] == "... 4 lines omitted ..."
    assert len(result) == 4


def failed_group(
    name: str,
    tests: int = 1,
    *,
    message: str = "boom",
    stack_trace: str | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> FailureGroup:
    attempt = Attempt(
        status=Status.FAILED,
        message=message,
        stack_trace=stack_trace,
        stdout=stdout,
        stderr=stderr,
    )
    results = [
        TestResult(
            test_id=f"tests/test_{name}.py::test_{n}", status=Status.FAILED, attempts=[attempt]
        )
        for n in range(tests)
    ]
    signature = Signature(exception_type="ValueError", message=name, frame=None)
    return FailureGroup(signature=signature, results=results)


def test_payload_fields_are_truncated_with_the_configured_limits() -> None:
    group = failed_group(
        "long",
        message="m" * 1005,
        stack_trace=numbered(100),
        stdout=numbered(100),
        stderr=numbered(100),
    )

    [payload] = build_payloads([group], Limits()).sent

    assert payload.message == "m" * 1000 + "... 5 characters omitted"
    assert payload.stack_trace is not None
    assert len(payload.stack_trace.splitlines()) == 15 + 1 + 25
    assert payload.stdout is not None
    assert payload.stdout.splitlines()[0] == "... 70 lines omitted ..."
    assert payload.stdout.splitlines()[-1] == "line 100"
    assert len(payload.stdout.splitlines()) == 1 + 30
    assert payload.stderr == payload.stdout


def test_limits_are_inputs() -> None:
    group = failed_group("long", stack_trace=numbered(10))

    [payload] = build_payloads([group], Limits(stack_head=1, stack_tail=1)).sent

    assert payload.stack_trace == "line 1\n... 8 lines omitted ...\nline 10"


def test_missing_fields_stay_missing() -> None:
    [payload] = build_payloads([failed_group("bare")], Limits()).sent

    assert payload.stack_trace is None
    assert payload.stdout is None
    assert payload.stderr is None
    assert payload.diff is None


def test_the_payload_describes_the_last_failed_attempt_of_the_first_test() -> None:
    first = Attempt(status=Status.FAILED, message="first try")
    second = Attempt(status=Status.FAILED, message="second try")
    third = Attempt(status=Status.PASSED)
    result = TestResult(
        test_id="tests/test_a.py::test_a",
        status=Status.PASSED_ON_RETRY,
        attempts=[first, second, third],
    )
    group = FailureGroup(
        signature=Signature(exception_type="", message="m", frame=None), results=[result]
    )

    [payload] = build_payloads([group], Limits()).sent

    assert payload.message == "second try"


def test_the_largest_groups_are_sent_first_and_the_rest_are_skipped_with_a_note() -> None:
    groups = [failed_group(f"g{size}", tests=size) for size in (1, 5, 3, 2, 4)]

    plan = build_payloads(groups, Limits(max_groups=3))

    assert [len(p.tests) for p in plan.sent] == [5, 4, 3]
    assert [len(s.group.results) for s in plan.skipped] == [2, 1]
    assert {s.note for s in plan.skipped} == {GROUP_CAP_NOTE}
    assert GROUP_CAP_NOTE == "not sent to LLM: group cap"


def test_groups_at_the_cap_are_all_sent() -> None:
    plan = build_payloads([failed_group("a"), failed_group("b")], Limits(max_groups=2))

    assert len(plan.sent) == 2
    assert plan.skipped == []


def test_no_groups_means_nothing_to_send() -> None:
    plan = build_payloads([], Limits())

    assert plan.sent == []
    assert plan.skipped == []


def test_a_cap_of_zero_sends_nothing() -> None:
    plan = build_payloads([failed_group("a")], Limits(max_groups=0))

    assert plan.sent == []
    assert len(plan.skipped) == 1


def test_a_secret_on_the_cut_is_redacted_before_truncation() -> None:
    message = "a" * 990 + " user@example.com " + "b" * 50

    [payload] = build_payloads([failed_group("leak", message=message)], Limits()).sent

    assert payload.message is not None
    assert "user" not in payload.message
    assert "example" not in payload.message
    assert "<EMAIL>" in payload.message


def test_the_diff_is_redacted_and_limited_to_files_in_the_trace() -> None:
    diff = DIFF.replace("+balance = 0.0\n", "+balance = 0.0\n+API_KEY = 'hunter2hunter2'\n")
    group = failed_group("diff", stack_trace=TRACE)

    [payload] = build_payloads([group], Limits(), diff=diff).sent

    assert payload.diff is not None
    assert "src/wallet/ledger.py" in payload.diff
    assert "README.md" not in payload.diff
    assert "API_KEY" in payload.diff
    assert "hunter2" not in payload.diff


def test_signals_in_the_payload_come_from_redacted_text() -> None:
    group = failed_group("net", message="ConnectionError for user@example.com")

    [payload] = build_payloads([group], Limits()).sent

    assert payload.signals
    assert all("user@example.com" not in s.quote for s in payload.signals)


def test_a_diff_without_relevant_files_is_left_out() -> None:
    group = failed_group("other", stack_trace="File other.py, line 1")

    [payload] = build_payloads([group], Limits(), diff=DIFF).sent

    assert payload.diff is None


def test_test_ids_in_the_payload_are_redacted() -> None:
    group = failed_group("ids")
    group.results[0].test_id = "tests/test_ids.py::test_mail[user@example.com]"

    [payload] = build_payloads([group], Limits()).sent

    assert payload.tests == ["tests/test_ids.py::test_mail[<EMAIL>]"]


def test_a_path_only_matches_whole_path_components() -> None:
    diff = DIFF.replace("ledger.py", "ger.py")
    trace = 'File "/work/src/wallet/ledger.py", line 9'

    assert select_hunks(diff, trace, limit=150) == ""


def test_trace_stdout_and_stderr_are_redacted() -> None:
    secret = "token=hunter2hunter2"
    group = failed_group("out", stack_trace=secret, stdout=secret, stderr=secret)

    [payload] = build_payloads([group], Limits()).sent

    for text in (payload.stack_trace, payload.stdout, payload.stderr):
        assert text is not None
        assert "hunter2" not in text
