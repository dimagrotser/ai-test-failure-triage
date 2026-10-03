from pathlib import Path

import pytest

from failtriage.parsers.junit import parse_junit
from failtriage.redaction import redact_result

CASES = sorted((Path(__file__).parent.parent / "evals" / "cases").glob("*/junit.xml"))


@pytest.mark.parametrize("junit", CASES, ids=lambda p: p.parent.name)
def test_lab_cases_keep_their_diagnostic_text(junit: Path) -> None:
    results = parse_junit(junit)

    redacted = [redact_result(r) for r in results]

    # Lab cases hold no secrets, so redaction must not eat the evidence a classifier needs.
    # A change here means a rule is too greedy or a case leaks something real-looking.
    assert redacted == results


def test_lab_cases_exist() -> None:
    assert CASES
