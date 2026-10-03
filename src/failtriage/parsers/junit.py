import xml.etree.ElementTree as ET
from pathlib import Path

from failtriage.models import Attempt, Status, TestResult


def parse_junit(path: Path) -> list[TestResult]:
    root = ET.parse(path).getroot()
    results: list[TestResult] = []
    for case in root.iter("testcase"):
        failure = case.find("failure")
        attempt = Attempt(
            status=Status.FAILED if failure is not None else Status.PASSED,
            message=failure.get("message") if failure is not None else None,
            stack_trace=failure.text if failure is not None else None,
        )
        test_id = f"{case.get('classname')}::{case.get('name')}"
        results.append(TestResult(test_id=test_id, status=attempt.status, attempts=[attempt]))
    return results
