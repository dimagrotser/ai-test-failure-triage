from collections import Counter
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from failtriage.classify.heuristics import classify_with_heuristics
from failtriage.grouping import group_failures
from failtriage.models import Category
from failtriage.parsers import ReportParseError
from failtriage.parsers.junit import parse_junit
from failtriage.redaction import redact_result


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

    @property
    def correct(self) -> bool:
        return self.expected is self.predicted


class EvalResult(BaseModel):
    cases: int
    groups: list[ScoredGroup]


def evaluate(evals_dir: Path) -> EvalResult:
    """Classify every failure group of every Lab case with heuristics only and score it."""
    case_dirs = sorted(p for p in (evals_dir / "cases").glob("*") if p.is_dir())
    if not case_dirs:
        raise EvalError(f"no cases found in {evals_dir / 'cases'}")
    groups = [group for case_dir in case_dirs for group in _score_case(case_dir)]
    return EvalResult(cases=len(case_dirs), groups=groups)


def _score_case(case_dir: Path) -> list[ScoredGroup]:
    label = _read_label(case_dir)
    try:
        results = [redact_result(r) for r in parse_junit(case_dir / "junit.xml")]
    except ReportParseError as exc:
        raise EvalError(f"case {case_dir.name}: {exc}") from exc
    groups = group_failures(results)
    if not groups:
        raise EvalError(f"case {case_dir.name}: junit.xml has no failures to classify")
    return [
        ScoredGroup(
            case_id=case_dir.name,
            source=label.source,
            expected=label.category,
            predicted=classify_with_heuristics(group).category,
        )
        for group in groups
    ]


def _read_label(case_dir: Path) -> Label:
    try:
        return Label.model_validate(yaml.safe_load((case_dir / "label.yaml").read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise EvalError(f"case {case_dir.name}: label.yaml is missing or invalid") from exc


def render_eval(result: EvalResult) -> str:
    groups = result.groups
    correct = sum(g.correct for g in groups)
    by_category = {c.value: [g for g in groups if g.expected is c] for c in Category}
    by_source = {s.value: [g for g in groups if g.source is s] for s in Source}
    lines = [
        f"Heuristics-only baseline: {result.cases} cases, {len(groups)} failure groups",
        f"Accuracy: {correct}/{len(groups)} ({_percent(correct, len(groups))})",
        "",
        *_accuracy_table("Category", by_category),
        "",
        *_accuracy_table("Source", by_source),
        "",
        *_confusion_matrix(groups),
        "",
        *_misses(groups),
    ]
    return "\n".join(lines) + "\n"


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
