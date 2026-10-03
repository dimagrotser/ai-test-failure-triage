from failtriage.models import Attempt, TestResult
from failtriage.redaction.patterns import RULES


def redact(text: str) -> str:
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_result(result: TestResult) -> TestResult:
    """Return a copy of the result with every text field of every attempt redacted."""
    return result.model_copy(update={"attempts": [_redact_attempt(a) for a in result.attempts]})


def _redact_attempt(attempt: Attempt) -> Attempt:
    return attempt.model_copy(
        update={
            field: redact(value)
            for field in ("message", "stack_trace", "stdout", "stderr")
            if (value := getattr(attempt, field)) is not None
        }
    )
