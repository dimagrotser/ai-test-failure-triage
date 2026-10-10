import json
import shutil
from pathlib import Path

import pytest

from failtriage.classify.provider import ProviderError, Usage
from failtriage.evaluate import EvalError, EvalReport, evaluate, render_eval
from failtriage.models import Category, ClassifiedBy, Confidence
from failtriage.prompts import Prompt

DATASET = Path(__file__).parent / "fixtures" / "evals"


def _row(output: str, name: str) -> list[str]:
    return next(line.split() for line in output.splitlines() if line.startswith(name + " "))


def test_every_failure_group_is_scored_against_the_label_of_its_case() -> None:
    result = evaluate(DATASET)

    scored = {(g.case_id, g.predicted) for g in result.groups}
    assert scored == {
        ("flaky-retry", Category.FLAKY),
        ("environment-refused", Category.ENVIRONMENT),
        ("product-bug-assert", Category.UNKNOWN),
        ("test-bug-two-groups", Category.TEST_BUG),
        ("test-bug-two-groups", Category.UNKNOWN),
    }


def test_accuracy_is_reported_per_category_with_weak_ones_included() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "5 failure groups" in output
    assert "Accuracy: 3/5 (60%)" in output
    assert _row(output, "flaky") == ["flaky", "1", "1", "100%"]
    assert _row(output, "product_bug") == ["product_bug", "1", "0", "0%"]
    assert _row(output, "test_bug") == ["test_bug", "2", "1", "50%"]


def test_a_category_without_cases_is_listed_with_a_dash() -> None:
    output = render_eval([evaluate(DATASET)])

    assert _row(output, "unknown") == ["unknown", "0", "0", "-"]


def test_accuracy_is_reported_per_source() -> None:
    output = render_eval([evaluate(DATASET)])

    assert _row(output, "injected") == ["injected", "2", "2", "100%"]
    assert _row(output, "mutation") == ["mutation", "1", "0", "0%"]
    assert _row(output, "real") == ["real", "2", "1", "50%"]


def _dataset_with_a_hidden_flaky_case(tmp_path: Path) -> Path:
    shutil.copytree(DATASET, tmp_path / "evals")
    cases = tmp_path / "evals" / "cases"
    shutil.copytree(cases / "flaky-retry", cases / "flaky-retry-hidden")
    label = cases / "flaky-retry-hidden" / "label.yaml"
    label.write_text(label.read_text() + "diff_shows_cause: false\n")
    return tmp_path / "evals"


def test_a_case_shows_its_cause_in_the_diff_unless_its_label_says_otherwise(
    tmp_path: Path,
) -> None:
    run = evaluate(_dataset_with_a_hidden_flaky_case(tmp_path))

    by_case = {g.case_id: g.diff_shows_cause for g in run.groups}
    assert by_case["flaky-retry-hidden"] is False
    assert [case for case, shows in by_case.items() if shows] == [
        "environment-refused",
        "flaky-retry",
        "product-bug-assert",
        "test-bug-two-groups",
    ]


def test_flaky_is_split_by_whether_the_diff_shows_the_cause(tmp_path: Path) -> None:
    output = render_eval([evaluate(_dataset_with_a_hidden_flaky_case(tmp_path))])

    assert _row(output, "in")[:3] == ["in", "the", "diff"]
    assert _row(output, "not")[:3] == ["not", "in", "diff"]
    assert "Flaky, cause" in output


def test_the_split_is_left_out_when_no_flaky_case_hides_its_cause() -> None:
    assert "Flaky, cause" not in render_eval([evaluate(DATASET)])


def test_a_source_without_cases_is_listed_with_a_dash(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    output = render_eval([evaluate(tmp_path)])

    assert _row(output, "mutation") == ["mutation", "0", "0", "-"]
    assert _row(output, "real") == ["real", "0", "0", "-"]


def test_confusion_matrix_counts_label_against_prediction() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "Confusion matrix (rows: label, columns: predicted)" in output
    matrix = output.split("Confusion matrix")[1].split("Misses")[0]
    assert _row(matrix, "product_bug") == ["product_bug", "0", "0", "0", "0", "1"]
    assert _row(matrix, "test_bug") == ["test_bug", "0", "1", "0", "0", "1"]
    assert _row(matrix, "flaky") == ["flaky", "0", "0", "1", "0", "0"]


def test_misses_name_the_case_and_both_categories() -> None:
    output = render_eval([evaluate(DATASET)])

    misses = output.split("Misses")[1]
    assert "product-bug-assert: product_bug -> unknown" in misses
    assert "test-bug-two-groups: test_bug -> unknown" in misses
    assert "flaky-retry" not in misses


def test_no_misses_is_said_plainly(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    assert "Misses: none" in render_eval([evaluate(tmp_path)])


def _dataset(tmp_path: Path, label: str | None, junit: str | None) -> Path:
    case = tmp_path / "cases" / "broken-case"
    case.mkdir(parents=True)
    if label is not None:
        (case / "label.yaml").write_text(label)
    if junit is not None:
        (case / "junit.xml").write_text(junit)
    return tmp_path


GREEN = '<testsuites><testsuite><testcase classname="t" name="ok"/></testsuite></testsuites>'


@pytest.mark.parametrize(
    ("label", "junit"),
    [
        (None, GREEN),
        ("category: nonsense\nsource: injected\n", GREEN),
        ("category: flaky\nsource: invented\n", GREEN),
        ("category: flaky\nsource: injected\n", None),
        ("category: flaky\nsource: injected\n", "<not xml"),
        ("category: flaky\nsource: injected\n", GREEN),
    ],
    ids=["no-label", "bad-category", "bad-source", "no-junit", "broken-junit", "no-failures"],
)
def test_a_broken_case_stops_the_run_and_is_named(
    tmp_path: Path, label: str | None, junit: str | None
) -> None:
    with pytest.raises(EvalError, match="broken-case"):
        evaluate(_dataset(tmp_path, label, junit))


def test_a_dataset_without_cases_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="no cases"):
        evaluate(tmp_path)


def _with_history(tmp_path: Path, history: str) -> Path:
    case = tmp_path / "cases" / "product-bug-assert"
    shutil.copytree(DATASET / "cases" / "product-bug-assert", case)
    (case / "history.json").write_text(history)
    return tmp_path


def test_the_history_of_a_case_reaches_the_heuristics(tmp_path: Path) -> None:
    test_id = "tests.test_fees::test_fee_rounds_half_up"
    runs = [
        {"test_id": test_id, "status": status, "attempts": 1, "sha": "a" * 40, "run_id": run}
        for run, status in enumerate(["passed", "failed"], start=1)
    ]

    result = evaluate(_with_history(tmp_path, json.dumps(runs)))

    assert [g.predicted for g in result.groups] == [Category.FLAKY]


def test_a_case_without_history_is_scored_cold(tmp_path: Path) -> None:
    result = evaluate(_with_history(tmp_path, "[]"))

    assert [g.predicted for g in result.groups] == [Category.UNKNOWN]


def test_an_invalid_history_stops_the_run_and_is_named(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="product-bug-assert.*history.json"):
        evaluate(_with_history(tmp_path, "not json"))


def test_every_scored_group_keeps_its_confidence_and_who_classified_it() -> None:
    result = evaluate(DATASET)

    by_case = {(g.case_id, g.predicted): (g.confidence, g.classified_by) for g in result.groups}
    assert by_case[("flaky-retry", Category.FLAKY)] == (Confidence.HIGH, ClassifiedBy.HEURISTICS)
    assert by_case[("product-bug-assert", Category.UNKNOWN)][0] is Confidence.LOW


def test_the_run_states_the_share_of_unknown_and_accuracy_among_high_confidence() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "Predicted unknown: 2/5 (40%)" in output
    assert "High confidence: 1/1 correct (100%)" in output


def test_a_run_without_high_confidence_answers_says_so(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "environment-refused", tmp_path / "cases" / "e")

    assert "High confidence: no answers" in render_eval([evaluate(tmp_path)])


def test_categories_below_half_are_named_as_weak() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "Weak categories: product_bug 0%" in output


def test_no_weak_category_is_said_plainly(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    assert "Weak categories: none below 50%" in render_eval([evaluate(tmp_path)])


def test_the_small_size_of_real_is_stated() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "The real source has 2 groups from 1 case, too few to draw conclusions." in output


def test_a_dataset_without_real_cases_says_nothing_is_known_about_real_failures(
    tmp_path: Path,
) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    output = render_eval([evaluate(tmp_path)])

    assert "The real source has no cases yet" in output


REFUSED_QUOTE = "ConnectionRefusedError: [Errno 111] Connection refused"


class FakeProvider:
    """Answers every group as a confident environment problem and counts like the real one."""

    def __init__(self, model: str = "claude-sonnet-5-5", fail: bool = False) -> None:
        self.model = model
        self.fail = fail
        self.usage = Usage()
        self.payloads: list[str] = []

    def complete(self, prompt: Prompt, payload: str) -> str:
        self.payloads.append(payload)
        self.usage.add(Usage(calls=1, input_tokens=1000, output_tokens=200))
        if self.fail:
            raise ProviderError("down")
        return json.dumps(
            {
                "category": "environment",
                "confidence": "high",
                "summary": "The service refused the connection.",
                "evidence": [REFUSED_QUOTE],
                "next_step": "Check the service",
                "disagreement_reason": None,
            }
        )


def _one_case(tmp_path: Path, name: str = "environment-refused") -> Path:
    shutil.copytree(DATASET / "cases" / name, tmp_path / "cases" / name)
    return tmp_path


def test_the_llm_classifies_every_group_and_the_run_names_the_model(tmp_path: Path) -> None:
    run = evaluate(_one_case(tmp_path), FakeProvider())

    assert run.model == "claude-sonnet-5-5"
    assert [(g.predicted, g.classified_by) for g in run.groups] == [
        (Category.ENVIRONMENT, ClassifiedBy.LLM)
    ]


def test_tokens_and_cost_of_the_run_are_recorded(tmp_path: Path) -> None:
    run = evaluate(DATASET, FakeProvider())

    assert run.cost.model == "claude-sonnet-5-5"
    assert run.cost.llm_calls == 5
    assert (run.cost.input_tokens, run.cost.output_tokens) == (5000, 1000)
    assert run.cost.usd == pytest.approx(0.02)


def test_a_model_without_a_price_has_an_unknown_cost(tmp_path: Path) -> None:
    run = evaluate(_one_case(tmp_path), FakeProvider(model="home-made"))

    assert run.cost.usd is None


def test_a_group_the_llm_fails_on_keeps_the_heuristics_answer(tmp_path: Path) -> None:
    run = evaluate(_one_case(tmp_path), FakeProvider(fail=True))

    assert [(g.predicted, g.classified_by) for g in run.groups] == [
        (Category.ENVIRONMENT, ClassifiedBy.HEURISTICS)
    ]
    assert run.cost.llm_calls == 1


def test_the_run_says_how_many_groups_fell_back_to_the_heuristics(tmp_path: Path) -> None:
    output = render_eval([evaluate(_one_case(tmp_path), FakeProvider(fail=True))])

    assert "Classified by heuristics instead of the LLM: 1" in output


class FailsOnce(FakeProvider):
    def complete(self, prompt: Prompt, payload: str) -> str:
        self.fail = not self.payloads
        return super().complete(prompt, payload)


def test_a_failed_call_is_counted_with_its_reason(tmp_path: Path) -> None:
    run = evaluate(_one_case(tmp_path), FakeProvider(fail=True))

    assert run.fallbacks == {"down": 1}


def test_a_healthy_run_has_no_fallbacks_and_no_warning(tmp_path: Path) -> None:
    root = _one_case(tmp_path)
    run = evaluate(root, FakeProvider())

    assert run.fallbacks == {}
    assert "WARNING" not in render_eval([evaluate(root), run])


def test_the_report_starts_with_a_warning_that_names_the_model_and_the_reason(
    tmp_path: Path,
) -> None:
    root = _one_case(tmp_path)

    output = render_eval([evaluate(root), evaluate(root, FakeProvider("claude-haiku-4-5", True))])

    assert output.startswith("WARNING: claude-haiku-4-5 made no successful call")
    assert "1 x down" in output


def test_a_run_where_only_some_calls_failed_lists_the_reasons_without_a_warning() -> None:
    run = evaluate(DATASET, FailsOnce())

    output = render_eval([run])

    assert run.fallbacks == {"down": 1}
    assert "1 x down" in output
    assert "WARNING" not in output


def test_a_result_file_from_before_the_reasons_still_loads() -> None:
    old = Path(__file__).parent.parent / "evals" / "results" / "2026-10-06.json"

    report = EvalReport.model_validate_json(old.read_text())

    assert all(run.fallbacks == {} for run in report.runs)


def test_the_diff_of_a_case_reaches_the_llm(tmp_path: Path) -> None:
    root = _one_case(tmp_path)
    (root / "cases" / "environment-refused" / "diff.patch").write_text(
        "diff --git a/wallet/ledger.py b/wallet/ledger.py\n"
        "--- a/wallet/ledger.py\n+++ b/wallet/ledger.py\n@@ -1 +1 @@\n-old\n+retry_budget = 3\n"
    )
    provider = FakeProvider()

    evaluate(root, provider)

    assert "retry_budget = 3" in provider.payloads[0]
    assert "wallet/ledger.py" in provider.payloads[0]


def test_the_history_of_a_case_reaches_the_llm(tmp_path: Path) -> None:
    root = _one_case(tmp_path)
    history = [
        {
            "test_id": "tests.test_ledger::test_records_transfer",
            "status": "failed",
            "attempts": 1,
            "sha": "c" * 40,
            "run_id": 7,
        }
    ]
    (root / "cases" / "environment-refused" / "history.json").write_text(json.dumps(history))
    provider = FakeProvider()

    evaluate(root, provider)

    assert "failed on main in run 7" in provider.payloads[0]


def test_secrets_of_a_case_never_reach_the_llm(tmp_path: Path) -> None:
    root = _one_case(tmp_path)
    case = root / "cases" / "environment-refused"
    junit = (case / "junit.xml").read_text()
    leaky = junit.replace(
        "wallet/ledger.py:12: ConnectionRefusedError",
        "https://ci:hunter2pass@ledger.internal/api ConnectionRefusedError",
    )
    (case / "junit.xml").write_text(leaky)
    (case / "diff.patch").write_text(
        "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n"
        "-X=1\n+DB_URL=postgres://admin:s3cretpw@db.internal/app\n"
    )
    provider = FakeProvider()

    evaluate(root, provider)

    assert provider.payloads
    sent = "".join(provider.payloads)
    assert "hunter2pass" not in sent
    assert "s3cretpw" not in sent


def test_the_report_lists_tokens_and_cost_per_run_and_in_total() -> None:
    sonnet = evaluate(DATASET, FakeProvider("claude-sonnet-5-5"))
    haiku = evaluate(DATASET, FakeProvider("claude-haiku-4-5"))

    output = render_eval([evaluate(DATASET), sonnet, haiku])

    assert "Heuristics and LLM (claude-sonnet-5-5)" in output
    assert "Heuristics and LLM (claude-haiku-4-5)" in output
    assert "LLM: 5 calls, 5000 input tokens, 1000 output tokens, $0.0200" in output
    assert "LLM: 5 calls, 5000 input tokens, 1000 output tokens, $0.0100" in output
    assert "Total LLM cost: $0.0300" in output


def test_an_unpriced_model_makes_the_total_unknown() -> None:
    run = evaluate(DATASET, FakeProvider("home-made"))

    output = render_eval([run])

    assert "cost unknown, no price for this model" in output
    assert "Total LLM cost: unknown" in output


def test_a_heuristics_only_report_has_no_llm_cost_lines() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "LLM:" not in output
    assert "Total LLM cost" not in output


def test_a_file_deleted_by_the_diff_counts_as_changed(tmp_path: Path) -> None:
    root = _one_case(tmp_path)
    (root / "cases" / "environment-refused" / "diff.patch").write_text(
        "diff --git a/wallet/ledger.py b/wallet/ledger.py\ndeleted file mode 100644\n"
        "--- a/wallet/ledger.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    )
    provider = FakeProvider()

    evaluate(root, provider)

    assert "wallet/ledger.py is changed in this pull request" in provider.payloads[0]
