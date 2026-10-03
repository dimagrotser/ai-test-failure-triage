from pathlib import Path

import pytest

from failtriage.parsers.junit import parse_junit
from failtriage.redaction import redact_result

CASES = sorted((Path(__file__).parent.parent / "evals" / "cases").glob("*/junit.xml"))
LEAK = "\nAPI_TOKEN=hunter2 owner=jane.doe@example.com"
REDACTED_LEAK = "\nAPI_TOKEN=<SECRET> owner=<EMAIL>"


@pytest.mark.parametrize("junit", CASES, ids=lambda p: p.parent.name)
def test_lab_case_redaction_removes_secrets_and_keeps_the_evidence(junit: Path) -> None:
    results = parse_junit(junit)
    leaky = [
        r.model_copy(
            update={"attempts": [a.model_copy(update={"stderr": LEAK}) for a in r.attempts]}
        )
        for r in results
    ]

    redacted = [redact_result(r) for r in leaky]

    # Everything the case already said must survive; only the injected secrets change.
    for original, result in zip(results, redacted, strict=True):
        assert [a.model_copy(update={"stderr": None}) for a in result.attempts] == [
            a.model_copy(update={"stderr": None}) for a in original.attempts
        ]
        assert all(a.stderr == REDACTED_LEAK for a in result.attempts)


def test_lab_cases_exist() -> None:
    assert CASES
