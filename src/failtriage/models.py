from enum import StrEnum

from pydantic import BaseModel


class Status(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class Attempt(BaseModel):
    status: Status
    message: str | None = None
    stack_trace: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    duration: float | None = None


class TestResult(BaseModel):
    __test__ = False  # keeps pytest from collecting it as a test class

    test_id: str
    status: Status
    attempts: list[Attempt]
