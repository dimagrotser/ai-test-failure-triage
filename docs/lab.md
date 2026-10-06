# The failure lab

`evals/cases/` is the labeled dataset the evals run against. Nobody edits it by hand. `make lab` rebuilds all of it from `evals/lab/scenarios/`, and every case in it has a label that the build has checked.

```
make lab
```

The build runs every scenario against a small wallet app in `evals/lab/wallet/`, writes the cases into a staging directory and swaps it in for `evals/cases/` only when all of them pass. If one scenario fails its check, the build stops with a message naming it and `evals/cases/` stays as it was. A scenario you delete also disappears from the dataset on the next run.

Two runs on the same machine give byte-identical files. Timings, temp paths, ports and object addresses are normalized, pytest runs in a fixed environment, and flaky behavior depends on the attempt number, never on real randomness. The cases are not identical across operating systems: the error text of a refused connection contains an errno that differs between macOS and Linux. Rebuild on the machine you commit from.

## What a case contains

| File | Content |
| --- | --- |
| `junit.xml` | The failing run, as pytest wrote it |
| `diff.patch` | The change that broke the wallet |
| `history.json` | Earlier runs of the same tree, empty if the scenario says `history: none` |
| `label.yaml` | The ground-truth category, copied from `scenario.yaml` |

## Adding a scenario

1. Create `evals/lab/scenarios/<category>-<short-name>/`. The name becomes the case id.
2. Write `diff.patch`. Copy `evals/lab/wallet/` somewhere, make `git init` there, change it and save `git diff` as the patch. Paths must read `a/wallet/...` or `a/tests/...`.
3. Write `scenario.yaml`.
4. Run `make lab`.
5. Add a test to `tests/test_lab_build.py` that names the test which must fail and a phrase from its message.
6. Commit the scenario and the rebuilt `evals/cases/` together.

`scenario.yaml` needs four fields. `category` is one of `product_bug`, `test_bug`, `flaky`, `environment`, `unknown`. `source` is `injected` for everything written by hand and `mutation` for the cases `make lab-mutants` generates. `scenario` is one line saying what is wrong. `notes` explains why the label is right, and for `unknown` why nobody can tell. Some categories take one more field, listed below.

## What the build checks

[ADR 0004](adr/0004-lab-labels-by-counterfactual.md) has the reasoning. In short, a label is only written if the build can show the cause.

| Category | The patch may change | Check |
| --- | --- | --- |
| `product_bug` | `wallet/` only | The run fails. Reverting the patch turns it green. |
| `test_bug` | `tests/` only | The run fails. Reverting the patch turns it green. |
| `environment` | `wallet/` only | The run fails under `condition`. The same tree passes in a healthy environment. |
| `flaky` | `wallet/` and `tests/` | Fails without retries, passes on the first retry. Needs `kind`. |
| `unknown` | either | Fails and does not pass on retry. No counterfactual exists, by design. |

An `environment` scenario needs a `condition` from `evals/lab/environment.py`: `service_down`, `dns_failure`, `timeout`, `missing_env_var` or `read_only_dir`. A `flaky` scenario needs a `kind` that says where the nondeterminism comes from: `timing`, `randomness` or `order_dependence`. An `unknown` scenario can set `history: none` to model a cold start, with no earlier runs to compare against.

The build also rejects a patch that touches a directory its category must leave alone, and a wallet that already fails before the patch is applied.

## When a scenario is rejected

The message names the scenario and the check, for example `restoring the environment of environment-ledger-down does not turn the run green`. Fix the patch or the label. Do not loosen the check to make a scenario pass: a case with a label nobody verified is worse than no case.

## Mutation cases

Hand-written bugs reflect what I thought of. `mutmut` supplies bugs I did not choose: it mutates the wallet one small change at a time and runs the wallet tests against each mutant. A mutant the tests kill is a product bug by construction, because the app changed and the tests did not.

```
uv sync
make lab-mutants
```

This is optional and `make lab` does not need `mutmut`. The command runs `mutmut` on a copy of `evals/lab/wallet/` inside the same environment the cases use, so the ledger stub is up. It then writes `evals/lab/scenarios/product-bug-mutant-<module>-<function>/` with `source: mutation` and the mutant as `diff.patch`, and runs `make lab` to build the cases. Commit the scenarios and the rebuilt `evals/cases/` together. A second run on the same code produces no diff.

Only killed mutants are used. A surviving mutant is either equivalent (it changes nothing a test could see) or a gap in the wallet tests, and neither gives a failing run to label, so those are skipped. Timeouts and suspicious results are skipped too. Each candidate also has to pass the normal check: applied as a plain patch it fails the tests, and reverting it turns them green.

From each function the generator takes the killed mutant with the lowest number. The wallet has 7 mutated functions, so this gives 7 cases, far fewer than the mutants `mutmut` kills. The cases are a sample, and they follow whatever mutations `mutmut` happens to try: `quantize(None)` in the fee calculation is a real crash but not a bug anyone would write.

## Running the evals

```
uv run failtriage eval evals/
```

The command classifies every Failure group of every case with the heuristics only, compares it with the case's label and prints accuracy per category and per source, a confusion matrix and the list of misses. It needs no API key. A case with several groups counts once per group, so `test-bug-fixture-leak` contributes four rows. A case that cannot be scored (no label, a missing or broken `junit.xml`, no failures at all) stops the run and is named in the message.

Baseline on the current 38 cases, 48 groups. 40 groups have source `injected` and 8 have source `mutation`:

| Category | Groups | Correct |
|---|---|---|
| environment | 9 | 8 |
| flaky | 7 | 7 |
| unknown | 7 | 4 |
| product_bug | 16 | 7 |
| test_bug | 9 | 2 |

Overall 28 of 48, or 58%. By source, the injected groups score 23 of 40 and the mutation groups 5 of 8. The `real` source has no cases yet, so the output shows it with a dash.

The mutation misses repeat the known weak spots. `product-bug-mutant-accounts-account-deposit` and `product-bug-mutant-transfers-transfer` change `<=` to `<` in the amount check, so a test fails with `DID NOT RAISE` in the test file and the rules call it a test bug, like `product-bug-funds-check-ignores-fee`. `product-bug-mutant-accounts-account-init` turns the owner into `None`, which shows up as an assertion mismatch in a test and stays `unknown`.

The weak spot is every assertion failure. A failing frame in test code together with an assertion mismatch is left undecided on purpose, because the test or the product could be wrong, so those groups come out as `unknown`. That accounts for `product-bug-fee-rounding`, `test-bug-expected-value` and three of the four groups of `test-bug-fixture-leak`. `product-bug-fee-rate-typo` is the same problem at a larger scale: one wrong constant produces four groups, and all four are assertion failures.

The rules also go wrong where the frame is not the place of the mistake, and the last batch added several of these:

- `product-bug-funds-check-ignores-fee` fails with `DID NOT RAISE` in the test file, so the rules call it a test bug. The broken check is in `wallet/transfers.py`.
- `test-bug-wrong-exception-expected` and `test-bug-receipt-fixture-short-of-funds` go the other way. The exception is raised inside the app, so the rules call them product bugs, but the fault is in what the test expects or sets up.
- `test-bug-statement-relative-path` reads as an environment problem because the error is a missing file.
- `environment-ledger-url-default` has no network wording in the ValueError about an empty url and the frame is in source code, so the rules call it a product bug.

Some `unknown` cases get a verdict they should not have. `unknown-amount-with-comma` raises from source code, so it comes out as a product bug. `unknown-confirmation-window-boundary` says "timed out" about the product's own 900 second rule, which the timeout pattern takes for a slow service. `unknown-account-tier-keyword` fails with a TypeError in the test file and is called a test bug, although the product could just as well have been meant to use the other name. `unknown-amount-formatting` and the two oldest unknown cases stay undecided, as they should.

The heuristics never see the diff or the history yet, which is where the LLM is expected to help.
