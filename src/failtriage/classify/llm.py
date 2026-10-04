from pydantic import BaseModel, ValidationError

from failtriage.classify.payload import GroupPayload
from failtriage.classify.provider import Provider
from failtriage.models import Category, Classification, ClassifiedBy, Confidence
from failtriage.prompts import Prompt


class InvalidAnswerError(Exception):
    pass


class LlmAnswer(BaseModel):
    category: Category
    confidence: Confidence
    summary: str
    evidence: list[str]
    next_step: str
    disagreement_reason: str | None = None


def classify_with_llm(payload: GroupPayload, provider: Provider, prompt: Prompt) -> Classification:
    """Ask the provider to classify a group. The payload must already be redacted."""
    raw = provider.complete(prompt, payload.model_dump_json(indent=2))
    try:
        answer = LlmAnswer.model_validate_json(raw)
        verdict = payload.heuristic_verdict
        return Classification(
            category=answer.category,
            confidence=answer.confidence,
            summary=answer.summary,
            evidence=answer.evidence,
            next_step=answer.next_step,
            classified_by=ClassifiedBy.LLM,
            heuristic_verdict=verdict,
            agrees_with_heuristics=None if verdict is None else answer.category is verdict,
            disagreement_reason=answer.disagreement_reason,
        )
    except ValidationError as exc:
        # Only the error locations: the messages quote the model's output.
        where = ", ".join(sorted({".".join(map(str, e["loc"])) or "answer" for e in exc.errors()}))
        raise InvalidAnswerError(f"the answer does not fit the schema: {where}") from None
