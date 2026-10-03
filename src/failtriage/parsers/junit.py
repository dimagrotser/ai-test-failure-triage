import re
import xml.etree.ElementTree as ET
from pathlib import Path

from failtriage.models import Attempt, Status, TestResult

OUTCOME_TAGS = {
    "failure": Status.FAILED,
    "error": Status.ERROR,
    "skipped": Status.SKIPPED,
}


class ReportParseError(Exception):
    """The report file is missing, unreadable or not valid XML."""


def parse_junit(path: Path) -> list[TestResult]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ReportParseError(f"cannot read {path}: {exc}") from exc
    results: list[TestResult] = []
    for case in root.iter("testcase"):
        attempt = _parse_attempt(case)
        test_id = _test_id(case)
        results.append(TestResult(test_id=test_id, status=attempt.status, attempts=[attempt]))
    return results


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


def _test_id(case: ET.Element) -> str:
    source = case.get("file") or case.get("classname") or ""
    name = re.sub(r"\[.*\]$", "", case.get("name") or "")
    return f"{source}::{name}"
