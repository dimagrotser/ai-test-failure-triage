from pathlib import Path

import pytest
import typer
import yaml
from typer.testing import CliRunner

from evals.lab.build import LabError, build_all
from evals.lab.real import import_case, main
from failtriage.evaluate import evaluate
from failtriage.parsers import ReportParseError
from failtriage.parsers.junit import parse_junit

REPORT = Path(__file__).parent / "fixtures" / "junit" / "real-with-secrets.xml"
PLANTED = [
    "hunter2",
    "jane.doe@example.com",
    "4111 1111 1111 1111",
    "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "eyJhbGciOiJIUzI1NiJ9",
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
    "abc.def.ghi",
]


def test_import_writes_a_redacted_junit_that_still_shows_the_failure(tmp_path: Path) -> None:
    case = import_case(REPORT, "real-checkout-card", tmp_path)

    xml = (case / "junit.xml").read_text()
    assert [s for s in PLANTED if s in xml] == []
    for placeholder in ("EMAIL", "CARD", "PRIVATE_KEY", "TOKEN", "JWT", "SECRET"):
        assert f"&lt;{placeholder}&gt;" in xml
    assert 'name="API_TOKEN" value="&lt;SECRET&gt;"' in xml
    [failed] = [r for r in parse_junit(case / "junit.xml") if r.attempts[0].message]
    assert failed.test_id == "tests.test_checkout::test_pay_with_saved_card"
    assert "RuntimeError: charge failed" in (failed.attempts[0].stack_trace or "")


def test_label_is_a_skeleton_for_a_human_to_fill_in(tmp_path: Path) -> None:
    case = import_case(REPORT, "real-checkout-card", tmp_path)

    label = yaml.safe_load((case / "label.yaml").read_text())
    assert label == {"category": "", "source": "real", "scenario": "", "notes": ""}
    assert not (case / "history.json").exists()


def test_existing_case_id_is_refused(tmp_path: Path) -> None:
    import_case(REPORT, "real-checkout-card", tmp_path)

    with pytest.raises(LabError, match="real-checkout-card already exists"):
        import_case(REPORT, "real-checkout-card", tmp_path)


def test_report_without_failures_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    green = REPORT.parent / "all_green.xml"

    with pytest.raises(LabError, match="no failures"):
        import_case(green, "real-green", tmp_path / "real")

    assert list((tmp_path / "real").iterdir()) == []


def test_broken_report_is_refused_without_quoting_it(tmp_path: Path) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><failure message='password=hunter2'>")

    with pytest.raises(ReportParseError) as exc:
        import_case(broken, "real-broken", tmp_path / "real")

    assert "hunter2" not in str(exc.value)
    assert not (tmp_path / "real" / "real-broken").exists()


def test_cli_prints_a_summary_and_no_planted_secret(tmp_path: Path) -> None:
    app = typer.Typer()
    app.command()(main)

    result = CliRunner().invoke(
        app, [str(REPORT), "real-checkout-card", "--real-dir", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert "2 tests, 1 groups" in result.output
    assert "fill label.yaml by hand" in result.output
    on_disk = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert [s for s in PLANTED if s in result.output + on_disk] == []


def test_cli_refusal_exits_2_and_prints_only_our_message(tmp_path: Path) -> None:
    app = typer.Typer()
    app.command()(main)
    args = [str(REPORT), "real-checkout-card", "--real-dir", str(tmp_path)]
    CliRunner().invoke(app, args)

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 2
    assert "already exists" in result.output


@pytest.fixture(autouse=True)
def _no_scenarios(tmp_path: Path) -> None:
    (tmp_path / "scenarios").mkdir()


def _fill_label(case: Path, category: str = "test_bug") -> None:
    (case / "label.yaml").write_text(
        yaml.safe_dump(
            {"category": category, "source": "real", "scenario": "card test", "notes": "why"}
        )
    )


def test_build_all_keeps_a_filled_real_case_next_to_the_scenarios(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _fill_label(import_case(REPORT, "real-checkout-card", real))

    build_all(tmp_path / "scenarios", tmp_path / "cases", real)

    assert (tmp_path / "cases" / "real-checkout-card" / "junit.xml").exists()
    [group] = evaluate(tmp_path).groups
    assert (group.source.value, group.expected.value) == ("real", "test_bug")


def test_build_all_rejects_a_real_case_nobody_labeled(tmp_path: Path) -> None:
    real = tmp_path / "real"
    import_case(REPORT, "real-checkout-card", real)
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "old.txt").write_text("kept")

    with pytest.raises(LabError, match="real-checkout-card has no category"):
        build_all(tmp_path / "scenarios", cases, real)

    assert (cases / "old.txt").read_text() == "kept"


def test_build_all_rejects_a_real_case_with_a_secret_added_after_the_import(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    case = import_case(REPORT, "real-checkout-card", real)
    _fill_label(case)
    junit = case / "junit.xml"
    junit.write_text(junit.read_text().replace("<testsuite ", '<testsuite owner="a@b.io" '))

    with pytest.raises(LabError, match="still contains redactable text") as exc:
        build_all(tmp_path / "scenarios", tmp_path / "cases", real)

    assert "a@b.io" not in str(exc.value)
