from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Status(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    PASSED_ON_RETRY = "passed_on_retry"


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


class Signature(BaseModel):
    model_config = ConfigDict(frozen=True)

    exception_type: str
    message: str
    frame: str | None


class FailureGroup(BaseModel):
    signature: Signature
    results: list[TestResult]


class Category(StrEnum):
    PRODUCT_BUG = "product_bug"
    TEST_BUG = "test_bug"
    FLAKY = "flaky"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


class SignalName(StrEnum):
    PASSED_ON_RETRY = "passed_on_retry"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    MISSING_ENV_OR_PERMISSION = "missing_env_or_permission"
    FRAME_IN_TEST_CODE = "frame_in_test_code"
    FRAME_IN_SOURCE_CODE = "frame_in_source_code"
    ASSERTION_MISMATCH = "assertion_mismatch"


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: SignalName
    quote: str
