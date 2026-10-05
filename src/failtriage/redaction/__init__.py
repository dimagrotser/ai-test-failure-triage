from failtriage.models import Attempt, Step, TestResult
from failtriage.redaction.patterns import RULES


def redact(text: str) -> str:
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_result(result: TestResult) -> TestResult:
    """Return a copy of the result with every text field of every attempt redacted."""
    return result.model_copy(update={"attempts": [_redact_attempt(a) for a in result.attempts]})


def _redact_attempt(attempt: Attempt) -> Attempt:
    update: dict[str, object] = {
        field: redact(value)
        for field in ("message", "stack_trace", "stdout", "stderr")
        if (value := getattr(attempt, field)) is not None
    }
    update["attachments"] = [redact(path) for path in attempt.attachments]
    update["steps"] = [_redact_step(s) for s in attempt.steps]
    return attempt.model_copy(update=update)


def _redact_step(step: Step) -> Step:
    return step.model_copy(
        update={
            "name": redact(step.name),
            "message": redact(step.message) if step.message is not None else None,
            "steps": [_redact_step(s) for s in step.steps],
        }
    )
