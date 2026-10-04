from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


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


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClassifiedBy(StrEnum):
    HEURISTICS = "heuristics"
    LLM = "llm"


class Classification(BaseModel):
    category: Category
    confidence: Confidence
    summary: str
    evidence: list[str]
    next_step: str
    classified_by: ClassifiedBy
    heuristic_verdict: Category | None
    # None when there is nothing to compare with: the heuristics classified the group itself,
    # or they had no verdict.
    agrees_with_heuristics: bool | None
    disagreement_reason: str | None = None

    @model_validator(mode="after")
    def _no_evidence_means_unknown(self) -> "Classification":
        if not self.evidence and (
            self.category is not Category.UNKNOWN or self.confidence is not Confidence.LOW
        ):
            raise ValueError(
                "a classification without evidence must be unknown with low confidence"
            )
        return self

    @model_validator(mode="after")
    def _disagreement_needs_a_reason(self) -> "Classification":
        disagrees = (
            self.heuristic_verdict is not None and self.category is not self.heuristic_verdict
        )
        if (
            self.classified_by is ClassifiedBy.LLM
            and disagrees
            and not (self.disagreement_reason or "").strip()
        ):
            raise ValueError("disagreement_reason is required when the llm disagrees")
        return self
