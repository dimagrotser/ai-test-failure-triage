import json
from pathlib import Path

import pytest

from failtriage.classify.llm import (
    InvalidAnswerError,
    UnredactedPayloadError,
    answer_schema,
    classify_groups,
    classify_with_llm,
)
from failtriage.classify.payload import GroupPayload, Limits, build_payloads
from failtriage.classify.provider import (
    MissingRecordingError,
    ProviderError,
    RecordedProvider,
    Usage,
)
from failtriage.github.pr_files import ChangedFile, to_unified_diff
from failtriage.grouping import group_failures
from failtriage.history import HistoryEntry
from failtriage.models import (
    Attempt,
    Category,
    ClassifiedBy,
    Confidence,
    FailureGroup,
    SignalName,
    Status,
    TestResult,
)
from failtriage.parsers.junit import parse_junit
from failtriage.prompts import Prompt, load_prompt

FIXTURES = Path(__file__).parent / "fixtures" / "evals" / "cases"
SECRETS = Path(__file__).parent / "fixtures" / "junit" / "secrets.xml"


class StubProvider:
    model = "stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.usage = Usage()
        self.payloads: list[str] = []

    def complete(self, prompt: Prompt, payload: str) -> str:
        self.payloads.append(payload)
        return self.answer


def payload_for(case: str) -> GroupPayload:
    groups = group_failures(parse_junit(FIXTURES / case / "junit.xml"))
    return build_payloads(groups, Limits()).sent[0]


def answer(**fields: object) -> str:
    base: dict[str, object] = {
        "category": "environment",
        "confidence": "medium",
        "summary": "The ledger service refused the connection.",
        "evidence": ["ConnectionRefusedError: [Errno 111] Connection refused"],
        "next_step": "Check that the ledger service is up",
        "disagreement_reason": None,
    }
    return json.dumps(base | fields)


def test_an_agreeing_answer_becomes_an_llm_classification() -> None:
    payload = payload_for("environment-refused")
    assert payload.heuristic_verdict is Category.ENVIRONMENT

    result = classify_with_llm(payload, StubProvider(answer()), load_prompt())

    assert result.category is Category.ENVIRONMENT
    assert result.confidence is Confidence.MEDIUM
    assert result.classified_by is ClassifiedBy.LLM
    assert result.heuristic_verdict is Category.ENVIRONMENT
    assert result.agrees_with_heuristics is True
    assert result.disagreement_reason is None
    assert result.evidence == ["ConnectionRefusedError: [Errno 111] Connection refused"]


def test_the_provider_gets_the_payload_as_json() -> None:
    payload = payload_for("environment-refused")
    provider = StubProvider(answer())

    classify_with_llm(payload, provider, load_prompt())

    assert GroupPayload.model_validate_json(provider.payloads[0]) == payload


def test_a_disagreement_with_a_reason_is_kept() -> None:
    reply = answer(
        category="test_bug", disagreement_reason="The test pins a port that changed in the diff"
    )

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.TEST_BUG
    assert result.agrees_with_heuristics is False
    assert result.disagreement_reason == "The test pins a port that changed in the diff"


def test_a_disagreement_without_a_reason_is_an_invalid_answer() -> None:
    reply = answer(category="test_bug")

    with pytest.raises(InvalidAnswerError):
        classify_with_llm(payload_for("environment-refused"), StubProvider(reply), load_prompt())


def test_a_group_without_a_heuristic_verdict_has_nothing_to_agree_with() -> None:
    payload = payload_for("environment-refused").model_copy(update={"heuristic_verdict": None})

    result = classify_with_llm(payload, StubProvider(answer()), load_prompt())

    assert result.heuristic_verdict is None
    assert result.agrees_with_heuristics is None


@pytest.mark.parametrize(
    "reply",
    ["not json", "{}", answer(category="network"), answer(evidence="a string")],
)
def test_an_answer_that_does_not_fit_the_schema_is_invalid(reply: str) -> None:
    with pytest.raises(InvalidAnswerError):
        classify_with_llm(payload_for("environment-refused"), StubProvider(reply), load_prompt())


def test_a_quote_that_is_not_in_the_payload_is_dropped() -> None:
    reply = answer(
        evidence=[
            "ConnectionRefusedError: [Errno 111] Connection refused",
            "Redis cluster failover at 03:14",
        ]
    )

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.ENVIRONMENT
    assert result.evidence == ["ConnectionRefusedError: [Errno 111] Connection refused"]


def test_a_quote_may_come_from_any_part_of_the_payload() -> None:
    payload = payload_for("environment-refused")
    quotes = [payload.signals[0].quote, payload.tests[0], "wallet/ledger.py:12"]

    result = classify_with_llm(payload, StubProvider(answer(evidence=quotes)), load_prompt())

    assert result.evidence == quotes


def test_a_quote_with_a_newline_is_checked_against_the_raw_text() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"stack_trace": "first line\nsecond line"}
    )

    result = classify_with_llm(
        payload, StubProvider(answer(evidence=["first line\nsecond line"])), load_prompt()
    )

    assert result.evidence == ["first line\nsecond line"]


def test_a_group_left_without_evidence_becomes_unknown_with_low_confidence() -> None:
    reply = answer(category="environment", confidence="high", evidence=["made up"])

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.confidence is Confidence.LOW
    assert result.evidence == []
    assert "not found" in result.summary


def test_dropping_all_evidence_explains_the_change_when_the_verdict_was_something_else() -> None:
    reply = answer(category="test_bug", evidence=["made up"], disagreement_reason="because")

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.agrees_with_heuristics is False
    assert result.disagreement_reason


def test_an_empty_quote_is_not_evidence() -> None:
    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(answer(evidence=[""])), load_prompt()
    )

    assert result.category is Category.UNKNOWN


def test_an_unknown_answer_without_evidence_stays_as_it_is() -> None:
    reply = answer(
        category="unknown", confidence="low", evidence=[], disagreement_reason="no proof"
    )

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.summary == "The ledger service refused the connection."


PLANTED = [
    "hunter2",
    "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "jane.doe@example.com",
    "4111 1111 1111 1111",
    "abc.def.ghi",
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
]


def test_planted_secrets_never_reach_the_provider() -> None:
    groups = group_failures(parse_junit(SECRETS))
    [payload] = build_payloads(groups, Limits()).sent
    reply = answer(
        category="unknown", confidence="low", evidence=[], disagreement_reason="no proof"
    )
    provider = StubProvider(reply)

    classify_with_llm(payload, provider, load_prompt())

    [sent] = provider.payloads
    assert [secret for secret in PLANTED if secret in sent] == []


def test_the_final_check_aborts_the_call_on_an_unredacted_payload() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"stderr": "GITHUB_TOKEN=ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"}
    )
    provider = StubProvider(answer())

    with pytest.raises(UnredactedPayloadError) as error:
        classify_with_llm(payload, provider, load_prompt())

    assert provider.payloads == []
    assert "ghp_" not in str(error.value)


def test_an_already_redacted_payload_passes_the_final_check() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"stderr": "GITHUB_TOKEN=<SECRET> for <EMAIL>"}
    )
    provider = StubProvider(answer())

    classify_with_llm(payload, provider, load_prompt())

    assert len(provider.payloads) == 1


RECORDINGS = Path(__file__).parent / "fixtures" / "llm"


def test_a_recorded_answer_is_replayed_into_a_classification() -> None:
    provider = RecordedProvider(RECORDINGS, "claude-sonnet-5-5")

    result = classify_with_llm(payload_for("environment-refused"), provider, load_prompt())

    assert result.category is Category.ENVIRONMENT
    assert result.agrees_with_heuristics is True


def test_a_recorded_answer_for_a_group_without_a_verdict() -> None:
    provider = RecordedProvider(RECORDINGS, "claude-sonnet-5-5")

    result = classify_with_llm(payload_for("product-bug-assert"), provider, load_prompt())

    assert result.category is Category.PRODUCT_BUG
    assert result.agrees_with_heuristics is None
    assert result.evidence == ["AssertionError: assert 1.49 == 1.5"]


def test_a_payload_without_a_recording_fails_instead_of_calling_out() -> None:
    provider = RecordedProvider(RECORDINGS, "claude-sonnet-5-5")
    payload = payload_for("flaky-retry")

    with pytest.raises(MissingRecordingError):
        classify_with_llm(payload, provider, load_prompt())


def test_an_unknown_answer_without_a_reason_is_kept_when_there_is_nothing_to_quote() -> None:
    reply = answer(category="unknown", confidence="low", evidence=[])

    result = classify_with_llm(
        payload_for("environment-refused"), StubProvider(reply), load_prompt()
    )

    assert result.category is Category.UNKNOWN
    assert result.agrees_with_heuristics is False
    assert result.disagreement_reason


def test_a_quote_copied_from_the_json_text_is_matched_and_stored_unescaped() -> None:
    payload = payload_for("environment-refused").model_copy(
        update={"message": 'assert x == {"a": 1}\\n'}
    )
    quote = 'assert x == {\\"a\\": 1}\\\\n'

    result = classify_with_llm(payload, StubProvider(answer(evidence=[quote])), load_prompt())

    assert result.evidence == ['assert x == {"a": 1}\\n']


def test_a_private_key_in_the_payload_aborts_the_call() -> None:
    key = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----"
    payload = payload_for("environment-refused").model_copy(update={"stderr": key})
    provider = StubProvider(answer())

    with pytest.raises(UnredactedPayloadError):
        classify_with_llm(payload, provider, load_prompt())

    assert provider.payloads == []


MIXED = Path(__file__).parent / "fixtures" / "junit" / "mixed.xml"
NO_PROOF = answer(category="unknown", confidence="low", evidence=[], disagreement_reason="no proof")


class FailingProvider(StubProvider):
    def complete(self, prompt: Prompt, payload: str) -> str:
        raise ProviderError("down")


def test_every_group_is_classified_by_the_llm_in_group_order() -> None:
    groups = group_failures(parse_junit(MIXED))
    provider = StubProvider(NO_PROOF)

    result = classify_groups(groups, provider, load_prompt(), Limits())

    assert len(provider.payloads) == 3
    assert [c.classified_by for c in result.classifications] == [ClassifiedBy.LLM] * 3
    assert result.failed == {}


def test_groups_over_the_cap_are_classified_by_heuristics_and_not_sent() -> None:
    groups = group_failures(parse_junit(MIXED))
    provider = StubProvider(NO_PROOF)

    result = classify_groups(groups, provider, load_prompt(), Limits(max_groups=1))

    assert len(provider.payloads) == 1
    by = [c.classified_by for c in result.classifications]
    assert by.count(ClassifiedBy.LLM) == 1
    assert by.count(ClassifiedBy.HEURISTICS) == 2
    assert result.failed == {}


def test_a_failing_call_falls_back_to_heuristics_and_records_only_the_error_type() -> None:
    groups = group_failures(parse_junit(MIXED))

    result = classify_groups(groups, FailingProvider(NO_PROOF), load_prompt(), Limits())

    assert [c.classified_by for c in result.classifications] == [ClassifiedBy.HEURISTICS] * 3
    assert result.failed == {0: "ProviderError", 1: "ProviderError", 2: "ProviderError"}


def test_an_invalid_answer_falls_back_for_that_group_only() -> None:
    groups = group_failures(parse_junit(MIXED))

    result = classify_groups(groups, StubProvider("not json"), load_prompt(), Limits())

    assert set(result.failed.values()) == {"InvalidAnswerError"}
    assert [c.classified_by for c in result.classifications] == [ClassifiedBy.HEURISTICS] * 3


def test_the_answer_schema_closes_the_object_and_requires_the_answer_fields() -> None:
    schema = answer_schema()

    assert schema["additionalProperties"] is False
    assert {"category", "confidence", "summary", "evidence", "next_step"} <= set(schema["required"])


TRACE = 'File "/work/src/wallet/fees.py", line 12, in fee\n    return round(amount * rate, 2)'


def fee_group() -> FailureGroup:
    attempt = Attempt(
        status=Status.FAILED, message="AssertionError: 1.49 != 1.48", stack_trace=TRACE
    )
    result = TestResult(
        test_id="tests/test_fees.py::test_fee", status=Status.FAILED, attempts=[attempt]
    )
    group = group_failures([result])[0]
    signature = group.signature.model_copy(update={"frame": "src/wallet/fees.py:fee"})
    return group.model_copy(update={"signature": signature})


def pr_diff(patch: str | None) -> tuple[str, list[str]]:
    files = [
        ChangedFile(filename="src/wallet/fees.py", status="modified", patch=patch),
        ChangedFile(filename="README.md", status="modified", patch="@@ -1 +1 @@\n-a\n+b"),
    ]
    return to_unified_diff(files), [f.filename for f in files]


def test_the_hunks_of_a_changed_file_reach_the_provider_with_the_signal() -> None:
    diff, changed = pr_diff("@@ -10,2 +10,2 @@\n-    rate = 1\n+    rate = 2")
    provider = StubProvider(NO_PROOF)

    classify_groups(
        [fee_group()], provider, load_prompt(), Limits(), diff=diff, changed_files=changed
    )

    sent = json.loads(provider.payloads[0])
    assert "+    rate = 2" in sent["diff"]
    assert "README.md" not in sent["diff"]
    assert SignalName.TOUCHES_CHANGED_FILE in {s["name"] for s in sent["signals"]}


def test_a_secret_in_a_patch_never_reaches_the_provider() -> None:
    diff, changed = pr_diff("@@ -1 +1 @@\n-x\n+API_KEY = 'hunter2hunter2'")
    provider = StubProvider(NO_PROOF)

    classify_groups(
        [fee_group()], provider, load_prompt(), Limits(), diff=diff, changed_files=changed
    )

    assert "hunter2" not in provider.payloads[0]
    assert "API_KEY" in provider.payloads[0]


def test_a_long_patch_is_truncated_before_it_reaches_the_provider() -> None:
    patch = "@@ -1,300 +1,300 @@\n" + "\n".join(f"+line {n}" for n in range(300))
    diff, changed = pr_diff(patch)
    provider = StubProvider(NO_PROOF)

    classify_groups(
        [fee_group()],
        provider,
        load_prompt(),
        Limits(diff_lines=20),
        diff=diff,
        changed_files=changed,
    )

    sent = json.loads(provider.payloads[0])
    assert "+line 299" not in sent["diff"]
    assert "lines omitted" in sent["diff"]


def test_a_file_without_a_patch_still_gives_the_signal_and_sends_no_diff() -> None:
    diff, changed = pr_diff(None)
    provider = StubProvider(NO_PROOF)

    classify_groups(
        [fee_group()], provider, load_prompt(), Limits(), diff=diff, changed_files=changed
    )

    sent = json.loads(provider.payloads[0])
    assert SignalName.TOUCHES_CHANGED_FILE in {s["name"] for s in sent["signals"]}
    assert "@@" not in (sent["diff"] or "")


def test_a_file_name_with_an_email_aborts_the_call_instead_of_leaking() -> None:
    provider = StubProvider(NO_PROOF)
    path = "src/wallet/fees.py"
    group = fee_group()

    result = classify_groups(
        [group], provider, load_prompt(), Limits(), changed_files=[path, "notes/jane@acme.io.md"]
    )
    assert provider.payloads  # a clean name is fine

    provider = StubProvider(NO_PROOF)
    leaky = group.model_copy(
        update={
            "signature": group.signature.model_copy(update={"frame": "notes/jane@acme.io.md:fee"})
        }
    )
    result = classify_groups(
        [leaky], provider, load_prompt(), Limits(), changed_files=["notes/jane@acme.io.md"]
    )

    assert provider.payloads == []
    assert result.failed == {0: "UnredactedPayloadError"}


def fee_history(test_id: str = "tests/test_fees.py::test_fee") -> list[HistoryEntry]:
    return [
        HistoryEntry(test_id=test_id, status=status, attempts=1, sha="d" * 40, run_id=run)
        for run, status in enumerate([Status.FAILED, Status.PASSED], start=1)
    ]


def test_history_signals_reach_the_provider() -> None:
    provider = StubProvider(NO_PROOF)

    classify_groups([fee_group()], provider, load_prompt(), Limits(), history=fee_history())

    sent = json.loads(provider.payloads[0])
    names = {s["name"] for s in sent["signals"]}
    assert {SignalName.FAILED_ON_MAIN, SignalName.FLAKY_IN_HISTORY} <= names
    assert sent["heuristic_verdict"] == Category.FLAKY


def test_a_secret_in_a_history_test_id_never_reaches_the_provider() -> None:
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    test_id = f"tests/test_fees.py::test_fee[{secret}]"
    group = fee_group()
    group.results[0].test_id = test_id
    provider = StubProvider(NO_PROOF)

    classify_groups([group], provider, load_prompt(), Limits(), history=fee_history(test_id))

    assert provider.payloads
    assert secret not in provider.payloads[0]


def test_a_group_over_the_cap_is_classified_with_the_history_too() -> None:
    small = fee_group()
    big = fee_group()
    big.results.append(big.results[0])
    big.signature = big.signature.model_copy(update={"message": "other"})

    result = classify_groups(
        [small, big],
        StubProvider(NO_PROOF),
        load_prompt(),
        Limits(max_groups=1),
        history=fee_history(),
    )

    assert result.classifications[0].category is Category.FLAKY
