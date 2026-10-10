import json
from collections.abc import Collection
from typing import Any

from pydantic import BaseModel, ValidationError

from failtriage.classify.heuristics import classify_with_heuristics
from failtriage.classify.payload import GroupPayload, Limits, build_payloads
from failtriage.classify.provider import Provider, ProviderError
from failtriage.history import HistoryEntry
from failtriage.models import (
    Category,
    Classification,
    ClassifiedBy,
    Confidence,
    FailureGroup,
    LlmSkip,
    SignalName,
)
from failtriage.prompts import Prompt
from failtriage.redaction import redact


class InvalidAnswerError(Exception):
    pass


NO_PROOF_REASON = "No quote from the failure output supports a more specific category"


class UnredactedPayloadError(Exception):
    pass


class LlmAnswer(BaseModel):
    category: Category
    confidence: Confidence
    summary: str
    evidence: list[str]
    next_step: str
    disagreement_reason: str | None = None


def answer_schema() -> dict[str, Any]:
    """JSON schema the provider asks the model to follow."""
    schema = LlmAnswer.model_json_schema()
    schema["additionalProperties"] = False
    return schema


class GroupClassifications(BaseModel):
    # In the order of the groups that were passed in.
    classifications: list[Classification]
    # Group index to why the group went to heuristics: the redacted reason of a failed call,
    # or the error type when the answer was unusable.
    failed: dict[int, str]


def classify_groups(
    groups: list[FailureGroup],
    provider: Provider,
    prompt: Prompt,
    limits: Limits,
    diff: str | None = None,
    changed_files: Collection[str] = (),
    history: Collection[HistoryEntry] = (),
) -> GroupClassifications:
    """Classify with the LLM what the group cap allows. The rest, and any group whose call or
    answer fails, gets the heuristics classification, so a run never ends without a result."""
    plan = build_payloads(groups, limits, diff, changed_files, history)
    payloads = {p.signature: p for p in plan.sent}
    classifications: list[Classification] = []
    failed: dict[int, str] = {}
    for index, group in enumerate(groups):
        payload = payloads.get(group.signature)
        if payload is None:
            classifications.append(_by_heuristics(group, history, LlmSkip(reason="group_cap")))
            continue
        try:
            classifications.append(classify_with_llm(payload, provider, prompt))
        except (ProviderError, InvalidAnswerError, UnredactedPayloadError) as exc:
            failed[index] = (
                redact(str(exc)) if isinstance(exc, ProviderError) else type(exc).__name__
            )
            skip = LlmSkip(reason="call_failed", detail=failed[index])
            classifications.append(_by_heuristics(group, history, skip))
    return GroupClassifications(classifications=classifications, failed=failed)


def _by_heuristics(
    group: FailureGroup, history: Collection[HistoryEntry], skip: LlmSkip
) -> Classification:
    return classify_with_heuristics(group, history).model_copy(update={"llm_skip": skip})


def classify_with_llm(payload: GroupPayload, provider: Provider, prompt: Prompt) -> Classification:
    """Ask the provider to classify a group. The payload must already be redacted."""
    text = payload.model_dump_json(indent=2)
    if redact(text) != text:
        # Second layer of ADR 0003: redaction already ran, so a change here means it missed
        # something. Nothing is quoted, the message must not leak the text either.
        raise UnredactedPayloadError("the payload still contains redactable text, call aborted")
    raw = provider.complete(prompt, text)
    try:
        answer = LlmAnswer.model_validate_json(raw)
        evidence = _verified(answer.evidence, payload)
        if not evidence and answer.category is not Category.UNKNOWN:
            answer = _without_proof(answer)
        elif not evidence:
            answer = answer.model_copy(
                update={"confidence": Confidence.LOW, "disagreement_reason": NO_PROOF_REASON}
            )
        verdict = payload.heuristic_verdict
        confidence = answer.confidence
        if answer.category is not verdict and _passed_on_retry(payload):
            # The model may overrule proof of nondeterminism, but two high answers that
            # contradict each other should not both read as certain.
            confidence = min(confidence, Confidence.MEDIUM, key=_CONFIDENCE_ORDER.index)
        return Classification(
            category=answer.category,
            confidence=confidence,
            summary=answer.summary,
            evidence=evidence,
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


_CONFIDENCE_ORDER = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]


def _passed_on_retry(payload: GroupPayload) -> bool:
    return payload.heuristic_verdict is Category.FLAKY and any(
        s.name is SignalName.PASSED_ON_RETRY for s in payload.signals
    )


def _verified(quotes: list[str], payload: GroupPayload) -> list[str]:
    """Keep the quotes that appear verbatim in the text the model was given."""
    texts = _texts(payload)
    kept = []
    for quote in quotes:
        # The model reads JSON, so it may copy a quote with the escapes that JSON adds.
        for candidate in (quote, _unescape(quote)):
            if candidate and any(candidate in text for text in texts):
                kept.append(candidate)
                break
    return kept


def _unescape(quote: str) -> str:
    try:
        text = json.loads(f'"{quote}"')
    except ValueError:
        return ""
    return text if isinstance(text, str) else ""


def _texts(payload: GroupPayload) -> list[str]:
    signature = payload.signature
    fields = [
        signature.exception_type,
        signature.message,
        signature.frame,
        payload.message,
        payload.stack_trace,
        payload.stdout,
        payload.stderr,
        payload.diff,
        *payload.tests,
        *(s.quote for s in payload.signals),
    ]
    return [f for f in fields if f]


def _without_proof(answer: LlmAnswer) -> LlmAnswer:
    return answer.model_copy(
        update={
            "category": Category.UNKNOWN,
            "confidence": Confidence.LOW,
            "summary": "The evidence the model quoted was not found in the failure output",
            "next_step": "Read the failure output, the quoted evidence could not be verified",
            "disagreement_reason": NO_PROOF_REASON,
        }
    )
