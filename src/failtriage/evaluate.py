import re
from collections import Counter
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from failtriage.classify.heuristics import classify_with_heuristics
from failtriage.classify.llm import classify_groups
from failtriage.classify.payload import Limits
from failtriage.classify.pricing import cost_usd
from failtriage.classify.provider import Provider
from failtriage.grouping import group_failures
from failtriage.history import HistoryEntry, HistoryError, load_history
from failtriage.models import Category, Classification, ClassifiedBy, Confidence, FailureGroup
from failtriage.parsers import ReportParseError
from failtriage.parsers.junit import parse_junit
from failtriage.prompts import load_prompt
from failtriage.redaction import redact_result
from failtriage.report.json_output import Cost

# Below this many groups a source says little about real-world accuracy.
_ENOUGH_REAL_GROUPS = 30
_WEAK_BELOW = 0.5
_DIFF_TARGET = re.compile(r"^diff --git a/.+ b/(.+)$", re.MULTILINE)


class EvalError(Exception):
    """The dataset cannot be scored, the message names the case."""


class Source(StrEnum):
    INJECTED = "injected"
    MUTATION = "mutation"
    REAL = "real"


class Label(BaseModel):
    category: Category
    source: Source


class ScoredGroup(BaseModel):
    case_id: str
    source: Source
    expected: Category
    predicted: Category
    confidence: Confidence
    classified_by: ClassifiedBy

    @property
    def correct(self) -> bool:
        return self.expected is self.predicted


class EvalRun(BaseModel):
    """One configuration scored on every case."""

    # Without a model the heuristics classified alone.
    model: str | None = None
    cases: int
    groups: list[ScoredGroup]
    cost: Cost = Cost()
    # Why the LLM did not classify a group, by reason. Results from before this field load as empty.
    fallbacks: dict[str, int] = {}


class EvalReport(BaseModel):
    """What `evals/results/<date>.json` holds."""

    date: str
    runs: list[EvalRun]


def evaluate(evals_dir: Path, provider: Provider | None = None) -> EvalRun:
    """Classify every failure group of every Lab case and score it. Without a provider the
    heuristics classify alone, with one the LLM gets the heuristic signals, the diff and the
    history of the case, as in a real run."""
    case_dirs = sorted(p for p in (evals_dir / "cases").glob("*") if p.is_dir())
    if not case_dirs:
        raise EvalError(f"no cases found in {evals_dir / 'cases'}")
    fallbacks: Counter[str] = Counter()
    groups = [
        group for case_dir in case_dirs for group in _score_case(case_dir, provider, fallbacks)
    ]
    if provider is None:
        return EvalRun(cases=len(case_dirs), groups=groups)
    usage = provider.usage
    cost = Cost(
        model=provider.model,
        llm_calls=usage.calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        usd=cost_usd(provider.model, usage),
    )
    return EvalRun(
        model=provider.model,
        cases=len(case_dirs),
        groups=groups,
        cost=cost,
        fallbacks=dict(fallbacks),
    )


def made_no_successful_call(run: EvalRun) -> bool:
    """A run of a model whose every group was classified by the heuristics scores like them."""
    return run.model is not None and all(
        g.classified_by is ClassifiedBy.HEURISTICS for g in run.groups
    )


def _score_case(
    case_dir: Path, provider: Provider | None, fallbacks: Counter[str]
) -> list[ScoredGroup]:
    label = _read_label(case_dir)
    try:
        results = [redact_result(r) for r in parse_junit(case_dir / "junit.xml")]
    except ReportParseError as exc:
        raise EvalError(f"case {case_dir.name}: {exc}") from exc
    groups = group_failures(results)
    if not groups:
        raise EvalError(f"case {case_dir.name}: junit.xml has no failures to classify")
    history = _read_history(case_dir)
    return [
        ScoredGroup(
            case_id=case_dir.name,
            source=label.source,
            expected=label.category,
            predicted=c.category,
            confidence=c.confidence,
            classified_by=c.classified_by,
        )
        for c in _classify(case_dir, groups, history, provider, fallbacks)
    ]


def _classify(
    case_dir: Path,
    groups: list[FailureGroup],
    history: list[HistoryEntry],
    provider: Provider | None,
    fallbacks: Counter[str],
) -> list[Classification]:
    if provider is None:
        return [classify_with_heuristics(group, history) for group in groups]
    diff = _read_diff(case_dir)
    changed = _DIFF_TARGET.findall(diff) if diff else []
    classified = classify_groups(groups, provider, load_prompt(), Limits(), diff, changed, history)
    fallbacks.update(classified.failed.values())
    return classified.classifications


def _read_diff(case_dir: Path) -> str | None:
    try:
        return (case_dir / "diff.patch").read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise EvalError(f"case {case_dir.name}: diff.patch cannot be read") from exc


def _read_history(case_dir: Path) -> list[HistoryEntry]:
    path = case_dir / "history.json"
    if not path.exists():
        return []
    try:
        return load_history(path)
    except HistoryError as exc:
        raise EvalError(f"case {case_dir.name}: history.json is not valid") from exc


def _read_label(case_dir: Path) -> Label:
    try:
        return Label.model_validate(yaml.safe_load((case_dir / "label.yaml").read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise EvalError(f"case {case_dir.name}: label.yaml is missing or invalid") from exc


def render_eval(runs: list[EvalRun]) -> str:
    lines: list[str] = [
        f"WARNING: {run.model} made no successful call, every group was classified by the "
        "heuristics, so its scores repeat the baseline."
        for run in runs
        if made_no_successful_call(run)
    ]
    lines += [""] if lines else []
    for run in runs:
        lines += [*_render_run(run), ""]
    lines += _total_cost(runs)
    lines += _real_note(runs[0].groups if runs else [])
    return "\n".join(lines) + "\n"


def _render_run(run: EvalRun) -> list[str]:
    groups = run.groups
    correct = sum(g.correct for g in groups)
    unknown = sum(g.predicted is Category.UNKNOWN for g in groups)
    high = [g for g in groups if g.confidence is Confidence.HIGH]
    by_category = {c.value: [g for g in groups if g.expected is c] for c in Category}
    by_source = {s.value: [g for g in groups if g.source is s] for s in Source}
    return [
        f"{_title(run)}: {run.cases} cases, {len(groups)} failure groups",
        f"Accuracy: {correct}/{len(groups)} ({_percent(correct, len(groups))})",
        f"Predicted unknown: {unknown}/{len(groups)} ({_percent(unknown, len(groups))})",
        _high_confidence(high),
        _weak_categories(by_category),
        *_fallbacks(run),
        "",
        *_accuracy_table("Category", by_category),
        "",
        *_accuracy_table("Source", by_source),
        "",
        *_confusion_matrix(groups),
        "",
        *_misses(groups),
    ]


def _fallbacks(run: EvalRun) -> list[str]:
    if run.model is None:
        return []
    fell_back = sum(g.classified_by is ClassifiedBy.HEURISTICS for g in run.groups)
    cost = run.cost
    price = f"${cost.usd:.4f}" if cost.usd is not None else "cost unknown, no price for this model"
    return [
        f"LLM: {cost.llm_calls} calls, {cost.input_tokens} input tokens, "
        f"{cost.output_tokens} output tokens, {price}",
        f"Classified by heuristics instead of the LLM: {fell_back}",
        *(f"  {n} x {reason}" for reason, n in sorted(run.fallbacks.items(), key=_by_count)),
    ]


def _by_count(item: tuple[str, int]) -> tuple[int, str]:
    return (-item[1], item[0])


def _total_cost(runs: list[EvalRun]) -> list[str]:
    costs = [run.cost for run in runs if run.model is not None]
    if not costs:
        return []
    known = [c.usd for c in costs if c.usd is not None]
    total = f"${sum(known):.4f}" if len(known) == len(costs) else "unknown"
    return [f"Total LLM cost: {total}", ""]


def _title(run: EvalRun) -> str:
    return "Heuristics-only" if run.model is None else f"Heuristics and LLM ({run.model})"


def _high_confidence(high: list[ScoredGroup]) -> str:
    if not high:
        return "High confidence: no answers"
    hits = sum(g.correct for g in high)
    return f"High confidence: {hits}/{len(high)} correct ({_percent(hits, len(high))})"


def _weak_categories(by_category: Mapping[str, list[ScoredGroup]]) -> str:
    weak = [
        f"{name} {_percent(sum(g.correct for g in found), len(found))}"
        for name, found in by_category.items()
        if found and sum(g.correct for g in found) / len(found) < _WEAK_BELOW
    ]
    return f"Weak categories: {', '.join(weak) if weak else f'none below {_WEAK_BELOW:.0%}'}"


def _real_note(groups: list[ScoredGroup]) -> list[str]:
    real = [g for g in groups if g.source is Source.REAL]
    if not real:
        return [
            "The real source has no cases yet, so nothing here shows how the classifier "
            "does on real failures."
        ]
    cases = len({g.case_id for g in real})
    sizes = f"{len(real)} groups from {cases} {'case' if cases == 1 else 'cases'}"
    verdict = "" if len(real) >= _ENOUGH_REAL_GROUPS else ", too few to draw conclusions"
    return [f"The real source has {sizes}{verdict}."]


def _percent(correct: int, total: int) -> str:
    return f"{round(100 * correct / total)}%" if total else "-"


def _accuracy_table(title: str, rows: Mapping[str, list[ScoredGroup]]) -> list[str]:
    lines = [f"{title:<13}{'Groups':>7}{'Correct':>9}{'Accuracy':>10}"]
    for key, found in rows.items():
        hits = sum(g.correct for g in found)
        lines.append(f"{key:<13}{len(found):>7}{hits:>9}{_percent(hits, len(found)):>10}")
    return lines


def _confusion_matrix(groups: list[ScoredGroup]) -> list[str]:
    counts = Counter((g.expected, g.predicted) for g in groups)
    lines = [
        "Confusion matrix (rows: label, columns: predicted)",
        f"{'':<13}" + "".join(f"{c:>13}" for c in Category),
    ]
    for label in Category:
        cells = "".join(f"{counts[(label, predicted)]:>13}" for predicted in Category)
        lines.append(f"{label:<13}{cells}")
    return lines


def _misses(groups: list[ScoredGroup]) -> list[str]:
    misses = Counter((g.case_id, g.expected, g.predicted) for g in groups if not g.correct)
    if not misses:
        return ["Misses: none"]
    lines = ["Misses"]
    for (case_id, expected, predicted), count in misses.items():
        suffix = f" ({count} groups)" if count > 1 else ""
        lines.append(f"{case_id}: {expected} -> {predicted}{suffix}")
    return lines
