from pathlib import Path

import yaml

from evals.lab.mutate import write_scenarios

MUTMUT = Path(__file__).parent / "fixtures" / "lab" / "mutmut"


def _show(name: str) -> str:
    return (MUTMUT / "show" / f"{name}.diff").read_text()


def _results(*statuses: str) -> str:
    lines = (MUTMUT / "results.txt").read_text().splitlines()
    return "\n".join(line for line in lines if line.rsplit(": ", 1)[1] in statuses)


def test_killed_mutant_becomes_a_mutation_scenario_with_a_git_patch(tmp_path: Path) -> None:
    written = write_scenarios(_results("killed"), _show, tmp_path)

    scenario = tmp_path / "product-bug-mutant-receipt-render-receipt"
    assert scenario in written
    label = yaml.safe_load((scenario / "scenario.yaml").read_text())
    assert label["category"] == "product_bug"
    assert label["source"] == "mutation"
    assert "wallet/receipt.py" in label["scenario"]
    assert "x_render_receipt__mutmut_1" in label["notes"]
    patch = (scenario / "diff.patch").read_text()
    assert patch.startswith("--- a/wallet/receipt.py\n+++ b/wallet/receipt.py\n@@ -")
    assert '+        f\'<p>Fee: <span data-testid="transfer-fee">{transfer_fee(None)}' in patch


def test_only_mutants_the_tests_killed_are_used(tmp_path: Path) -> None:
    survivors = _results("survived", "timeout", "suspicious")

    assert write_scenarios(survivors, _show, tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_one_mutant_per_function_the_lowest_numbered(tmp_path: Path) -> None:
    written = write_scenarios(_results("killed"), _show, tmp_path)

    fees = tmp_path / "product-bug-mutant-fees-transfer-fee"
    assert fees in written
    assert (
        "x_transfer_fee__mutmut_1" in yaml.safe_load((fees / "scenario.yaml").read_text())["notes"]
    )
    assert "quantize(None" in (fees / "diff.patch").read_text()


def test_mutant_that_does_not_fail_the_tests_as_a_plain_patch_is_dropped(tmp_path: Path) -> None:
    written = write_scenarios(_results("killed"), _show, tmp_path)

    assert tmp_path / "product-bug-mutant-ledger-record-transfer" not in written
    assert not (tmp_path / "product-bug-mutant-ledger-record-transfer").exists()


def test_rerun_replaces_old_mutant_scenarios_and_keeps_the_others(tmp_path: Path) -> None:
    (tmp_path / "product-bug-mutant-gone-function").mkdir()
    (tmp_path / "product-bug-fee-rounding").mkdir()

    write_scenarios(_results("killed"), _show, tmp_path)

    assert not (tmp_path / "product-bug-mutant-gone-function").exists()
    assert (tmp_path / "product-bug-fee-rounding").is_dir()


def test_mutant_in_a_method_is_matched_although_mutmut_drops_the_class_indent(
    tmp_path: Path,
) -> None:
    write_scenarios(_results("killed"), _show, tmp_path)

    patch = (tmp_path / "product-bug-mutant-accounts-account-deposit" / "diff.patch").read_text()
    assert "-        if amount <= 0:\n+        if amount < 0:\n" in patch
