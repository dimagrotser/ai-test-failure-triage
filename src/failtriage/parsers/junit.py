import re
import xml.etree.ElementTree as ET
from pathlib import Path

from failtriage.models import Attempt, Status, TestResult
from failtriage.parsers import ReportParseError, final_status

OUTCOME_TAGS = {
    "failure": Status.FAILED,
    "error": Status.ERROR,
    "skipped": Status.SKIPPED,
}


# Surefire writes a retried test as one testcase: flaky* elements are failed attempts before
# the final pass, rerun* elements are failed attempts after the first failure.
FLAKY_TAGS = {"flakyFailure": Status.FAILED, "flakyError": Status.ERROR}
RERUN_TAGS = {"rerunFailure": Status.FAILED, "rerunError": Status.ERROR}


def parse_junit(path: Path) -> list[TestResult]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ReportParseError(f"cannot read {path}: {exc}") from exc
    attempts_by_case: dict[tuple[str, str, str], list[Attempt]] = {}
    reruns = _plain_reruns(root)
    for case in root.iter("testcase"):
        attempts_by_case.setdefault(_key(case), []).extend(_parse_attempts(case, case in reruns))
    return [
        TestResult(test_id=_test_id(*key), status=final_status(attempts), attempts=attempts)
        for key, attempts in attempts_by_case.items()
    ]


def _key(case: ET.Element) -> tuple[str, str, str]:
    return (case.get("file") or "", case.get("classname") or "", case.get("name") or "")


def _plain_reruns(root: ET.Element) -> set[ET.Element]:
    """Testcases that pytest-rerunfailures or flaky wrote for a failed attempt they then retried.

    Such an attempt is a testcase with no outcome that the suite leaves out of its `tests` count.
    A suite with more testcases than it counts proves retries only when the extra ones are exactly
    the earlier copies of repeated testcases. Any other mismatch says nothing, so nothing changes.
    """
    reruns: set[ET.Element] = set()
    for suite in root.iter("testsuite"):
        declared = suite.get("tests")
        cases = suite.findall("testcase")
        if declared is None or not declared.isdigit() or len(cases) <= int(declared):
            continue
        copies: dict[tuple[str, str, str], list[ET.Element]] = {}
        for case in cases:
            copies.setdefault(_key(case), []).append(case)
        earlier = [case for group in copies.values() for case in group[:-1]]
        if len(earlier) == len(cases) - int(declared) and not any(map(_has_outcome, earlier)):
            reruns.update(earlier)
    return reruns


def _has_outcome(case: ET.Element) -> bool:
    return any(case.find(tag) is not None for tag in (*OUTCOME_TAGS, *FLAKY_TAGS, *RERUN_TAGS))


def _parse_attempts(case: ET.Element, was_rerun: bool) -> list[Attempt]:
    before = [_parse_retry(el, st) for tag, st in FLAKY_TAGS.items() for el in case.findall(tag)]
    after = [_parse_retry(el, st) for tag, st in RERUN_TAGS.items() for el in case.findall(tag)]
    attempt = _parse_attempt(case)
    if was_rerun:
        attempt = attempt.model_copy(update={"status": Status.FAILED})
    return [*before, attempt, *after]


def _parse_retry(element: ET.Element, status: Status) -> Attempt:
    return Attempt(
        status=status,
        message=element.get("message"),
        stack_trace=element.findtext("stackTrace") or element.text,
        stdout=element.findtext("system-out"),
        stderr=element.findtext("system-err"),
        duration=_parse_duration(element.get("time")),
    )


def _parse_attempt(case: ET.Element) -> Attempt:
    status = Status.PASSED
    message = None
    stack_trace = None
    for tag, tag_status in OUTCOME_TAGS.items():
        outcome = case.find(tag)
        if outcome is not None:
            status = tag_status
            message = outcome.get("message")
            stack_trace = outcome.text
            break
    return Attempt(
        status=status,
        message=message,
        stack_trace=stack_trace,
        stdout=case.findtext("system-out"),
        stderr=case.findtext("system-err"),
        duration=_parse_duration(case.get("time")),
    )


def _parse_duration(raw: str | None) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _test_id(file: str, classname: str, name: str) -> str:
    return f"{file or classname}::{re.sub(r'\[.*\]$', '', name)}"
